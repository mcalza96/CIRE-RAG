import logging
from typing import Dict, Any, Optional
from uuid import UUID

from app.domain.types.ingestion_status import IngestionStatus

logger = logging.getLogger(__name__)

from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.supabase.client import get_async_supabase_client
import os
from app.core.observability.correlation import get_correlation_id
from app.services.database.taxonomy_manager import TaxonomyManager
from app.core.settings import settings


class InstitutionalIngestionUseCase:
    """
    Use Case for Institutional Ingestion.
    
    DEPRECATED: The old LangGraph pipeline (institutional_ingest/nodes.py) is replaced
    by the unified PreProcessedContentStrategy which uses heading-based semantic chunking.
    """
    def __init__(self):
        self.repo = SupabaseSourceRepository()
        self.taxonomy_manager = TaxonomyManager()

    async def execute(self, tenant_id: str, file_path: str, document_id: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Prepares and queues institutional ingestion for the Worker pipeline.
        This ensures parity with manual ingestion and enables all new tooling:
        Graph enrichment, atomic upsert, metrics, retries, and community jobs.
        """
        logger.info(f"Triggering Unified Institutional Ingestion for Doc: {document_id}, Tenant: {tenant_id}")
        
        try:
            # 1. Determine strategy up-front so first QUEUED write already contains it.
            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
            strategy_key = "PRE_PROCESSED" if ext in ("md", "txt") else "CONTENT"

            # 2. Create Source Traceability Record
            merged_metadata = {
                "status": IngestionStatus.QUEUED.value,
                "institution_id": tenant_id,
                "is_global": False,
                "storage_path": file_path,
                "storage_bucket": settings.RAG_STORAGE_BUCKET,
                "correlation_id": get_correlation_id(),
                "strategy_override": strategy_key,
            }
            if metadata:
                merged_metadata.update(metadata)

            incoming_nested = (metadata or {}).get("metadata")
            if isinstance(incoming_nested, dict):
                merged_metadata.setdefault("metadata", {})
                if isinstance(merged_metadata["metadata"], dict):
                    merged_metadata["metadata"].update(incoming_nested)

            raw_nested_meta = merged_metadata.get("metadata")
            nested_meta: Dict[str, Any] = raw_nested_meta if isinstance(raw_nested_meta, dict) else {}
            nested_meta["strategy_override"] = strategy_key
            nested_meta["embedding_mode"] = (metadata or {}).get("embedding_mode", "LOCAL")
            merged_metadata["metadata"] = nested_meta

            collection_key = (
                merged_metadata.get("collection_key")
                or nested_meta.get("collection_key")
                or merged_metadata.get("collection_id")
                or nested_meta.get("collection_id")
            )
            collection_name = merged_metadata.get("collection_name") or nested_meta.get("collection_name")
            collection_id = None
            if collection_key:
                collection = await self.taxonomy_manager.ensure_collection_open(
                    tenant_id=str(tenant_id),
                    collection_key=str(collection_key),
                    collection_name=str(collection_name) if collection_name else None,
                )
                collection_id = collection.get("id")
                nested_meta["collection_key"] = collection.get("collection_key")
                nested_meta["collection_name"] = collection.get("name")
                nested_meta["collection_id"] = collection_id
                merged_metadata["collection_id"] = collection_id
                merged_metadata["collection_name"] = collection.get("name")

            # 1.1 Ensure source_document exists without forcing invalid course_id FKs
            client = await get_async_supabase_client()
            upsert_payload = {
                "id": str(document_id),
                "filename": os.path.basename(file_path),
                "status": IngestionStatus.QUEUED.value,
                "metadata": merged_metadata,
                "institution_id": str(tenant_id),
            }
            if collection_id:
                upsert_payload["collection_id"] = str(collection_id)

            course_id_raw = (metadata or {}).get("course_id")
            if course_id_raw:
                try:
                    upsert_payload["course_id"] = str(UUID(str(course_id_raw)))
                except Exception:
                    logger.warning(f"Ignoring invalid course_id in institutional metadata: {course_id_raw}")

            try:
                await client.table("source_documents").upsert(upsert_payload).execute()
            except Exception as e:
                # Defensive retry if a provided course_id breaks FK
                if "source_documents_course_id_fkey" in str(e) and "course_id" in upsert_payload:
                    logger.warning("course_id FK failed; retrying source_document upsert without course_id")
                    upsert_payload.pop("course_id", None)
                    await client.table("source_documents").upsert(upsert_payload).execute()
                else:
                    raise

            logger.info(
                "Routing institutional ingest via worker strategy %s (ext=%s tenant=%s)",
                strategy_key,
                ext,
                tenant_id,
            )

            # 3. Ensure document remains queued with latest metadata.
            await self.repo.update_status_and_metadata(
                document_id,
                IngestionStatus.QUEUED.value,
                {
                    **merged_metadata,
                    "strategy": strategy_key,
                    "embedding_mode": (metadata or {}).get("embedding_mode", "LOCAL"),
                },
            )

            return {
                "status": "accepted",
                "message": "Institutional ingestion queued (worker pipeline)",
                "document_id": document_id,
                "queued": True,
                "strategy": strategy_key,
            }
            
        except Exception as e:
            logger.error(f"Failed to prepare institutional ingestion: {e}")
            raise e
