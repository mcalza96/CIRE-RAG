from typing import Dict, Any, Optional
import structlog
import os

from app.domain.types.ingestion_status import IngestionStatus
from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.services.manual_ingestion_query_service import (
    ManualIngestionQueryService,
)
from app.application.services.ingestion_backpressure_service import IngestionBackpressureService
from app.application.services.ingestion_batch_service import IngestionBatchService
from app.infrastructure.observability.correlation import get_correlation_id
from app.services.database.taxonomy_manager import TaxonomyManager
from app.infrastructure.settings import settings
from app.utils.filename_utils import sanitize_filename

logger = structlog.get_logger(__name__)


class InstitutionalIngestionUseCase:
    """
    Use Case for Institutional Ingestion.

    DEPRECATED: The old LangGraph pipeline (institutional_ingest/nodes.py) is replaced
    by the unified PreProcessedContentStrategy which uses heading-based semantic chunking.
    """

    def __init__(
        self,
        repo: Optional[SupabaseSourceRepository] = None,
        query_service: Optional[ManualIngestionQueryService] = None,
        taxonomy_manager: Optional[TaxonomyManager] = None,
        backpressure_service: Optional[IngestionBackpressureService] = None,
        batch_service: Optional[IngestionBatchService] = None,
    ):
        self.repo = repo or SupabaseSourceRepository()
        shared_query = query_service or ManualIngestionQueryService()
        shared_taxonomy = taxonomy_manager or TaxonomyManager()
        self.batch_service = batch_service or IngestionBatchService(shared_query, shared_taxonomy)
        self.query_service = self.batch_service.query_service
        self.taxonomy_manager = self.batch_service.taxonomy_manager
        self.backpressure = backpressure_service or IngestionBackpressureService(self.query_service)

    async def execute(
        self,
        tenant_id: str,
        file_path: str,
        document_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Prepares and queues institutional ingestion for the Worker pipeline.
        This ensures parity with manual ingestion and enables all new tooling:
        Graph enrichment, atomic upsert, metrics, retries, and community jobs.
        """
        logger.info(
            "trigger_unified_institutional_ingestion", document_id=document_id, tenant_id=tenant_id
        )

        try:
            await self.backpressure.enforce_limit(tenant_id=str(tenant_id))

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
            nested_meta: Dict[str, Any] = (
                raw_nested_meta if isinstance(raw_nested_meta, dict) else {}
            )
            nested_meta["strategy_override"] = strategy_key
            nested_meta["embedding_mode"] = (metadata or {}).get("embedding_mode", "LOCAL")
            merged_metadata["metadata"] = nested_meta

            collection_key = (
                merged_metadata.get("collection_key")
                or nested_meta.get("collection_key")
                or merged_metadata.get("collection_id")
                or nested_meta.get("collection_id")
            )
            collection_name = merged_metadata.get("collection_name") or nested_meta.get(
                "collection_name"
            )
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

            filename = os.path.basename(file_path)
            sanitized_filename = sanitize_filename(filename)

            # 1.1 Ensure source_document exists without forcing invalid course_id FKs
            await self.query_service.upsert_source_document(
                document_id=str(document_id),
                filename=sanitized_filename,
                tenant_id=str(tenant_id),
                metadata=merged_metadata,
                collection_id=str(collection_id) if collection_id else None,
                course_id=str((metadata or {}).get("course_id"))
                if (metadata or {}).get("course_id")
                else None,
            )

            logger.info(
                "institutional_ingest_routed",
                strategy=strategy_key,
                extension=ext,
                tenant_id=tenant_id,
                document_id=document_id,
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
                "queue": await self.backpressure.get_pending_snapshot(tenant_id=str(tenant_id)),
            }

        except Exception as e:
            logger.error("institutional_ingest_preparation_failed", error=str(e), exc_info=True)
            raise e
