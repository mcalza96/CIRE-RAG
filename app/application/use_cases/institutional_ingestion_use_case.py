import logging
import math
from typing import Dict, Any, Optional

from app.domain.types.ingestion_status import IngestionStatus

logger = logging.getLogger(__name__)

from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.services.manual_ingestion_query_service import ManualIngestionQueryService
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
        self.query_service = ManualIngestionQueryService()

    async def _get_pending_snapshot(self, tenant_id: str) -> Dict[str, Optional[int]]:
        max_pending = int(getattr(settings, "INGESTION_MAX_PENDING_PER_TENANT", 0) or 0)
        limit = max_pending + 1 if max_pending > 0 else 1000
        pending_count = await self.query_service.count_pending_documents(
            tenant_id=str(tenant_id),
            limit=limit,
            statuses=["queued", "pending", "pending_ingestion", "processing", "processing_v2"],
        )
        docs_per_minute_per_worker = max(1, int(getattr(settings, "INGESTION_DOCS_PER_MINUTE_PER_WORKER", 2) or 2))
        worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 1) or 1))
        throughput_per_minute = docs_per_minute_per_worker * worker_concurrency
        estimated_wait_seconds = int(math.ceil((pending_count / throughput_per_minute) * 60)) if pending_count > 0 else 0

        return {
            "queue_depth": pending_count,
            "max_pending": max_pending if max_pending > 0 else None,
            "estimated_wait_seconds": estimated_wait_seconds,
        }

    async def _enforce_pending_limit(self, tenant_id: str) -> Dict[str, Optional[int]]:
        snapshot = await self._get_pending_snapshot(tenant_id=tenant_id)
        max_pending = int(snapshot.get("max_pending") or 0)
        if max_pending <= 0:
            return snapshot

        pending_count = int(snapshot.get("queue_depth") or 0)
        if pending_count >= max_pending:
            raise ValueError(
                "INGESTION_BACKPRESSURE "
                f"tenant={tenant_id} pending={pending_count} max={max_pending} "
                f"eta_seconds={int(snapshot.get('estimated_wait_seconds') or 0)}"
            )

        return snapshot

    async def execute(self, tenant_id: str, file_path: str, document_id: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Prepares and queues institutional ingestion for the Worker pipeline.
        This ensures parity with manual ingestion and enables all new tooling:
        Graph enrichment, atomic upsert, metrics, retries, and community jobs.
        """
        logger.info(f"Triggering Unified Institutional Ingestion for Doc: {document_id}, Tenant: {tenant_id}")
        
        try:
            await self._enforce_pending_limit(tenant_id=str(tenant_id))

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
            await self.query_service.upsert_source_document(
                document_id=str(document_id),
                filename=os.path.basename(file_path),
                tenant_id=str(tenant_id),
                metadata=merged_metadata,
                collection_id=str(collection_id) if collection_id else None,
                course_id=str((metadata or {}).get("course_id")) if (metadata or {}).get("course_id") else None,
            )

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
                "queue": await self._get_pending_snapshot(tenant_id=str(tenant_id)),
            }
            
        except Exception as e:
            logger.error(f"Failed to prepare institutional ingestion: {e}")
            raise e
