import os
import shutil
import tempfile
import structlog
from uuid import uuid4
from typing import Optional, Dict, Any, Tuple
from fastapi import UploadFile

from app.domain.ingestion.types import IngestionStatus
from app.domain.schemas.ingestion_schemas import IngestionMetadata
from app.infrastructure.supabase.repositories.taxonomy_repository import TaxonomyRepository
from app.infrastructure.supabase.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.supabase.queries.ingestion_query_service import ManualIngestionQueryService
from app.infrastructure.observability.ingestion.backpressure import IngestionBackpressureService
from app.infrastructure.settings import settings
from app.infrastructure.filesystem.filename_utils import sanitize_filename
from app.infrastructure.observability.correlation import get_correlation_id

logger = structlog.get_logger(__name__)

class IngestionTrigger:
    """
    Workflow for triggering and preparing document ingestion.
    Consolidates logic for Manual and Institutional ingestion entry points.
    """
    def __init__(
        self,
        repo: Optional[SupabaseSourceRepository] = None,
        query_service: Optional[ManualIngestionQueryService] = None,
        taxonomy_manager: Optional[TaxonomyRepository] = None,
        backpressure_service: Optional[IngestionBackpressureService] = None,
    ):
        self.repo = repo or SupabaseSourceRepository()
        self.query_service = query_service or ManualIngestionQueryService()
        self.taxonomy_manager = taxonomy_manager or TaxonomyRepository()
        self.backpressure = backpressure_service or IngestionBackpressureService(self.query_service)

    async def prepare_manual_upload(self, file: UploadFile, metadata_str: str) -> Tuple[str, str, IngestionMetadata]:
        """Parses metadata and saves file to temp location."""
        try:
            parsed_metadata = IngestionMetadata.parse_raw(metadata_str)
            logger.info(f"[IngestionTrigger] Received manual upload: {file.filename}")
        except Exception as e:
            logger.error(f"[IngestionTrigger] Metadata parsing error: {e}")
            raise ValueError(f"Invalid metadata: {e}")

        tenant_id = str(parsed_metadata.institution_id) if parsed_metadata.institution_id else None
        extra_meta = parsed_metadata.metadata or {}
        collection_key_raw = extra_meta.get("collection_key") or extra_meta.get("collection_id")
        collection_name_raw = extra_meta.get("collection_name")
        
        if tenant_id and collection_key_raw:
            await self.taxonomy_manager.ensure_collection_open(
                tenant_id=tenant_id,
                collection_key=str(collection_key_raw),
                collection_name=str(collection_name_raw) if collection_name_raw else None,
            )

        temp_dir = tempfile.gettempdir()
        unique_id = uuid4()
        safe_filename = f"ingest_{unique_id}_{file.filename}"
        file_path = os.path.join(temp_dir, safe_filename)

        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            logger.error(f"[IngestionTrigger] Error saving temp file: {e}")
            raise RuntimeError("Could not save uploaded file locally.")

        original_filename = file.filename or safe_filename
        return file_path, original_filename, parsed_metadata

    async def trigger_manual_ingestion(self, file_path: str, original_filename: str, metadata: IngestionMetadata) -> Dict[str, Any]:
        """Registers the document for asynchronous processing by the worker."""
        try:
            tenant_id = str(metadata.institution_id) if metadata.institution_id else None
            await self.backpressure.enforce_limit(tenant_id=tenant_id)

            metadata.storage_path = file_path
            doc_id = await self.taxonomy_manager.register_document(
                filename=original_filename,
                metadata=metadata,
                initial_status=IngestionStatus.QUEUED,
            )

            queue = await self.backpressure.get_pending_snapshot(tenant_id=tenant_id)
            return {"document_id": str(doc_id), "queue": queue}
        except Exception as e:
            logger.error(f"[IngestionTrigger] Registration failed for {original_filename}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            raise e

    async def trigger_institutional_ingestion(
        self,
        tenant_id: str,
        file_path: str,
        document_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Prepares and queues institutional ingestion (usually from webhook)."""
        logger.info("[IngestionTrigger] Triggering institutional ingestion", document_id=document_id, tenant_id=tenant_id)

        try:
            await self.backpressure.enforce_limit(tenant_id=str(tenant_id))

            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
            strategy_key = "PRE_PROCESSED" if ext in ("md", "txt") else "CONTENT"

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

            filename = os.path.basename(file_path)
            sanitized_filename = sanitize_filename(filename)

            await self.query_service.upsert_source_document(
                document_id=str(document_id),
                filename=sanitized_filename,
                tenant_id=str(tenant_id),
                metadata=merged_metadata,
                collection_id=str(collection_id) if collection_id else None,
                course_id=str((metadata or {}).get("course_id")) if (metadata or {}).get("course_id") else None,
            )

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
                "message": "Institutional ingestion queued",
                "document_id": document_id,
                "queued": True,
                "strategy": strategy_key,
                "queue": await self.backpressure.get_pending_snapshot(tenant_id=str(tenant_id)),
            }
        except Exception as e:
            logger.error("[IngestionTrigger] Institutional preparation failed", error=str(e), exc_info=True)
            raise e
