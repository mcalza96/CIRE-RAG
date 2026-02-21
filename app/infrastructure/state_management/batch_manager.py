import json
import structlog
from uuid import uuid4
from typing import Optional, Dict, Any, List
from fastapi import UploadFile

from app.infrastructure.settings import settings
from app.domain.types.ingestion_status import IngestionStatus
from app.infrastructure.supabase.queries.ingestion_query_service import ManualIngestionQueryService
from app.infrastructure.supabase.repositories.taxonomy_repository import TaxonomyRepository
from app.infrastructure.filesystem.filename_utils import sanitize_filename

logger = structlog.get_logger(__name__)

class IngestionBatchService:
    """
    Service responsible for managing ingestion batches, uploading files to storage,
    and synchronizing document records in Supabase.
    """
    def __init__(
        self, 
        query_service: Optional[ManualIngestionQueryService] = None,
        taxonomy_manager: Optional[TaxonomyRepository] = None
    ):
        self.query_service = query_service or ManualIngestionQueryService()
        self.taxonomy_manager = taxonomy_manager or TaxonomyRepository()

    async def create_batch(
        self,
        tenant_id: str,
        collection_key: str,
        total_files: int,
        auto_seal: bool = False,
        collection_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Creates a NEW ingestion batch and ensures the collection/tenant exist."""
        if total_files <= 0:
            raise ValueError("INVALID_TOTAL_FILES")

        tenant_name = metadata.get("tenant_name") if isinstance(metadata, dict) else None
        
        # 1. Ensure Infrastructure Context
        await self.taxonomy_manager.ensure_institution_exists(
            tenant_id=str(tenant_id),
            institution_name=str(tenant_name) if tenant_name else None,
        )

        collection = await self.taxonomy_manager.ensure_collection_open(
            tenant_id=str(tenant_id),
            collection_key=str(collection_key),
            collection_name=collection_name,
        )

        # 2. Persist Batch
        return await self.query_service.create_batch(
            tenant_id=str(tenant_id),
            collection_id=str(collection["id"]),
            collection_key=str(collection["collection_key"]),
            collection_name=str(collection.get("name") or collection["collection_key"]),
            total_files=int(total_files),
            auto_seal=bool(auto_seal),
            metadata=metadata,
        )

    async def add_file_to_batch(
        self,
        batch_id: str,
        file: UploadFile,
        metadata_json: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Uploads a file to storage and registers it in the batch.
        Checks for idempotency (same filename in same batch).
        """
        # 1. Fetch Batch Context
        context = await self.query_service.get_batch_upload_context(batch_id=str(batch_id))
        batch = context.get("batch")
        collection = context.get("collection")
        docs = context.get("documents") or []

        if not isinstance(batch, dict): raise ValueError("BATCH_NOT_FOUND")
        if not isinstance(collection, dict): raise ValueError("COLLECTION_NOT_FOUND")

        # 2. Idempotency Check
        for doc in docs:
            if not isinstance(doc, dict): continue
            if str(doc.get("filename")) == str(file.filename):
                return {
                    "doc_id": doc.get("id"),
                    "filename": doc.get("filename"),
                    "status": doc.get("status"),
                    "queued": False,
                    "idempotent": True
                }

        # 3. Limit Check
        if len(docs) >= int(batch.get("total_files") or 0):
            raise ValueError("BATCH_FILE_LIMIT_EXCEEDED")

        # 4. Prepare Metadata
        parsed_metadata: Dict[str, Any] = {}
        if metadata_json:
            try:
                parsed_metadata = json.loads(metadata_json)
            except Exception as e:
                raise ValueError(f"INVALID_METADATA_JSON: {e}")

        safe_filename = file.filename or f"document_{uuid4()}.bin"
        sanitized_fn = sanitize_filename(safe_filename)
        doc_id = str(uuid4())
        
        # Path: {tenant}/{collection}/{batch}/{uuid}_{filename}
        storage_path = f"{batch['tenant_id']}/{collection['collection_key']}/{batch_id}/{doc_id}_{sanitized_fn}"

        # 5. Upload to Storage
        content_bytes = await file.read()
        if not content_bytes:
            raise ValueError("EMPTY_FILE")

        target_bucket = settings.RAG_STORAGE_BUCKET
        await self.query_service.upload_to_storage(
            bucket=target_bucket,
            path=storage_path,
            content_bytes=content_bytes,
            content_type=file.content_type or "application/octet-stream",
        )

        # 6. Register Source Document
        merged_metadata = {
            "status": IngestionStatus.QUEUED.value,
            "institution_id": str(batch["tenant_id"]),
            "is_global": False,
            "storage_path": storage_path,
            "storage_bucket": target_bucket,
            "batch_id": str(batch_id),
            "collection_id": str(collection["id"]),
            "collection_key": str(collection["collection_key"]),
            "collection_name": str(collection.get("name") or collection.get("collection_key")),
            **parsed_metadata,
        }
        
        # Ensure 'metadata' nested dict has critical fields for the worker dispatcher
        nested = merged_metadata.setdefault("metadata", {})
        if isinstance(nested, dict):
            nested.update({
                "collection_id": str(collection["id"]),
                "collection_key": str(collection["collection_key"]),
                "collection_name": str(collection.get("name") or collection.get("collection_key")),
                "filename": safe_filename
            })

        insert_payload = {
            "id": doc_id,
            "filename": safe_filename,
            "status": IngestionStatus.QUEUED.value,
            "metadata": merged_metadata,
            "institution_id": str(batch["tenant_id"]),
            "collection_id": str(collection["id"]),
            "batch_id": str(batch_id),
        }
        
        await self.query_service.queue_source_document_for_batch(
            payload=insert_payload,
            batch_id=str(batch_id),
        )

        return {
            "doc_id": doc_id,
            "filename": safe_filename,
            "status": IngestionStatus.QUEUED.value,
            "queued": True,
            "idempotent": False,
        }

    async def seal_batch(self, batch_id: str) -> Dict[str, Any]:
        """Finalizes a batch, updates counters and marks status (completed/failed/partial)."""
        batch = await self.query_service.get_batch_for_seal(batch_id=str(batch_id))

        # Close collection if it was auto-managed
        collection_id = batch.get("collection_id")
        if collection_id:
            await self.query_service.seal_collection(collection_id=str(collection_id))

        # Calculate final results
        docs = await self.query_service.list_document_statuses_for_batch(batch_id=str(batch_id))
        
        success_states = {"success", "processed", "completed", "ready"}
        failed_states = {"failed", "error", "dead_letter"}

        completed, failed = 0, 0
        for row in docs:
            st = str(row.get("status") or "").lower()
            if st in success_states: completed += 1
            elif st in failed_states: failed += 1

        total_files = int(batch.get("total_files") or len(docs))
        if total_files <= 0: total_files = len(docs)

        # Batch Status Logic
        if completed + failed >= total_files and total_files > 0:
            if failed == 0: status = "completed"
            elif completed == 0: status = "failed"
            else: status = "partial"
        else:
            status = "processing"

        await self.query_service.update_batch_status_counters(
            batch_id=str(batch_id),
            completed=completed,
            failed=failed,
            status=status,
        )

        return {
            "batch_id": batch_id,
            "status": status,
            "completed": completed,
            "failed": failed,
            "total_files": total_files,
            "sealed": True,
        }
