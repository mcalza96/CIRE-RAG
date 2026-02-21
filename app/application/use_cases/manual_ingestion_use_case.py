import os
import json
import shutil
import tempfile
import structlog
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import UploadFile
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.domain.schemas.ingestion_schemas import IngestionMetadata
from app.services.database.taxonomy_manager import TaxonomyManager
from app.workflows.ingestion.mock_upload_file import MockUploadFile

logger = structlog.get_logger(__name__)

from app.domain.types.ingestion_status import IngestionStatus

from app.infrastructure.supabase.repositories.supabase_source_repository import SupabaseSourceRepository
from uuid import uuid4
from app.infrastructure.supabase.queries.ingestion_query_service import ManualIngestionQueryService
from app.infrastructure.supabase.client import get_async_supabase_client


from app.services.ingestion.monitoring.backpressure import IngestionBackpressureService
from app.services.ingestion.observability.ingestion_tracer import IngestionObservabilityService
from app.services.ingestion.state.batch_manager import IngestionBatchService

class ManualIngestionUseCase:
    def __init__(
        self, 
        dispatcher: IngestionDispatcher,
        backpressure_service: Optional[IngestionBackpressureService] = None,
        observability_service: Optional[IngestionObservabilityService] = None,
        batch_service: Optional[IngestionBatchService] = None
    ):
        self.dispatcher = dispatcher
        self.taxonomy_manager = TaxonomyManager()
        self.source_repo = SupabaseSourceRepository()
        self.query_service = ManualIngestionQueryService()
        
        # New specialized services
        self.backpressure = backpressure_service or IngestionBackpressureService(self.query_service)
        self.observability = observability_service or IngestionObservabilityService(self.query_service)
        self.batch = batch_service or IngestionBatchService(self.query_service, self.taxonomy_manager)
        
        # Legacy/Lazy
        self.raptor_processor = None

    async def get_queue_status(self, tenant_id: str) -> Dict[str, Optional[int]]:
        return await self.backpressure.get_pending_snapshot(tenant_id=tenant_id)

    async def get_documents(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Lists recently registered documents from Supabase.
        """
        return await self.query_service.list_recent_documents(limit=limit)

    async def list_collections(self, tenant_id: str) -> List[Dict[str, Any]]:
        return await self.query_service.list_collections(tenant_id=str(tenant_id))

    async def list_tenants(self, limit: int = 200) -> List[Dict[str, Any]]:
        return await self.query_service.list_tenants(limit=int(limit))

    async def cleanup_collection(self, tenant_id: str, collection_key: str) -> Dict[str, Any]:
        return await self.query_service.cleanup_collection(
            tenant_id=str(tenant_id),
            collection_key=str(collection_key),
        )

    async def retry_ingestion(self, doc_id: str):
        """
        Re-triggers background processing for an existing document.
        Updates the existing record to QUEUED status and increments retry_count.
        """
        # 1. Fetch document record
        doc = await self.source_repo.get_by_id(doc_id)
        if not doc:
            raise ValueError(f"Document {doc_id} not found.")

        current_metadata = doc.get("metadata", {}) or {}

        # 2. Increment Retry Count
        retry_count = current_metadata.get("retry_count", 0)
        new_retry_count = retry_count + 1

        # 3. Update Metadata and Status
        current_metadata["retry_count"] = new_retry_count

        # We also need to ensure the file is actually there if it's a local file.
        # But for 'retry', we rely on the worker to validate existence or fail again.
        # This keeps the API response fast.

        logger.info(f"[ManualIngest] Retrying document {doc_id} (Attempt {new_retry_count})")

        await self.source_repo.update_status_and_metadata(
            doc_id, IngestionStatus.QUEUED.value, current_metadata
        )

        return doc_id

    async def enqueue_deferred_enrichment(
        self,
        doc_id: str,
        *,
        tenant_id: str,
        include_visual: bool = True,
        include_graph: bool = True,
        include_raptor: bool = True,
    ) -> Dict[str, Any]:
        tenant = str(tenant_id or "").strip()
        if not tenant:
            raise ValueError("INVALID_TENANT_ID")

        doc = await self.source_repo.get_by_id(doc_id)
        if not doc:
            raise FileNotFoundError(f"Document {doc_id} not found.")

        doc_tenant = str(doc.get("institution_id") or "").strip()
        if doc_tenant and doc_tenant != tenant:
            raise ValueError("TENANT_MISMATCH")

        collection_id = doc.get("collection_id")
        payload = {
            "source_document_id": str(doc_id),
            "collection_id": str(collection_id) if collection_id else None,
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }

        client = await get_async_supabase_client()
        existing = await (
            client.table("job_queue")
            .select("id")
            .eq("job_type", "enrich_document")
            .in_("status", ["pending", "processing"])
            .contains("payload", {"source_document_id": str(doc_id)})
            .limit(1)
            .execute()
        )
        if isinstance(existing.data, list) and existing.data:
            existing_id = str(existing.data[0].get("id") or "").strip()
            return {
                "accepted": True,
                "already_queued": True,
                "job_id": existing_id or None,
                "source_document_id": str(doc_id),
                "include_visual": bool(include_visual),
                "include_graph": bool(include_graph),
                "include_raptor": bool(include_raptor),
            }

        inserted = await (
            client.table("job_queue")
            .insert(
                {
                    "job_type": "enrich_document",
                    "tenant_id": tenant,
                    "payload": payload,
                }
            )
            .execute()
        )
        inserted_rows = (
            inserted.data if inserted is not None and isinstance(inserted.data, list) else []
        )
        inserted_job_id = (
            str(inserted_rows[0].get("id") or "").strip()
            if inserted_rows and isinstance(inserted_rows[0], dict)
            else ""
        )

        if not inserted_job_id:
            fallback = await (
                client.table("job_queue")
                .select("id")
                .eq("job_type", "enrich_document")
                .eq("tenant_id", tenant)
                .in_("status", ["pending", "processing"])
                .contains("payload", {"source_document_id": str(doc_id)})
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            fallback_rows = fallback.data if isinstance(fallback.data, list) else []
            if fallback_rows and isinstance(fallback_rows[0], dict):
                inserted_job_id = str(fallback_rows[0].get("id") or "").strip()

        await self.source_repo.log_event(
            doc_id=str(doc_id),
            message=(
                "Enrichment replay queued "
                f"(visual={bool(include_visual)}, graph={bool(include_graph)}, raptor={bool(include_raptor)})"
            ),
            status="INFO",
            tenant_id=tenant,
            metadata={"phase": "enrichment_replay", **payload},
        )

        return {
            "accepted": True,
            "already_queued": False,
            "job_id": inserted_job_id or None,
            "source_document_id": str(doc_id),
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }

    async def get_job_status(self, *, tenant_id: str, job_id: str) -> Dict[str, Any]:
        tenant = str(tenant_id or "").strip()
        if not tenant:
            raise ValueError("INVALID_TENANT_ID")
        return await self.query_service.get_job_status(tenant_id=tenant, job_id=job_id)

    async def execute(self, file: UploadFile, metadata: str):
        """
        Processes a manual document ingestion request.
        """
        # 1. Parse Metadata
        try:
            parsed_metadata = IngestionMetadata.parse_raw(metadata)
            logger.info(f"[ManualIngest] Received ingestion for: {file.filename}")
        except Exception as e:
            logger.error(f"[ManualIngest] Metadata parsing error: {e}")
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

        # 2. Save to Temp Location
        # We must save it because background tasks might access it after the request closes
        temp_dir = tempfile.gettempdir()
        # FIX: Use unique filename to prevent collisions in concurrent uploads
        unique_id = uuid4()
        safe_filename = f"ingest_{unique_id}_{file.filename}"
        file_path = os.path.join(temp_dir, safe_filename)

        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            logger.error(f"[ManualIngest] Error saving temp file: {e}")
            raise RuntimeError("Could not save uploaded file locally.")

        # 3. Return file + metadata for queue registration.
        original_filename = file.filename or safe_filename
        return file_path, original_filename, parsed_metadata

    async def process_background(
        self, file_path: str, original_filename: str, metadata: IngestionMetadata
    ) -> Dict[str, Any]:
        """
        Registers document for async Worker processing.
        """
        try:
            tenant_id = str(metadata.institution_id) if metadata.institution_id else None
            await self.backpressure.enforce_limit(tenant_id=tenant_id)

            # 1. Register Document (Set to QUEUED to trigger Worker)
            metadata.storage_path = file_path # Inyectar path para el worker
            doc_id = await self.taxonomy_manager.register_document(
                filename=original_filename,
                metadata=metadata,
                initial_status=IngestionStatus.QUEUED,
            )

            queue = await self.backpressure.get_pending_snapshot(tenant_id=tenant_id)
            return {"document_id": str(doc_id), "queue": queue}

        except Exception as e:
            logger.error(f"[ManualIngest] Failed to register document {original_filename}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            raise e

    async def create_batch(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.batch.create_batch(*args, **kwargs)

    async def add_file_to_batch(self, batch_id: str, file: UploadFile, metadata: Optional[str] = None) -> Dict[str, Any]:
        # Orchestration: check limits before action
        batch_ctx = await self.query_service.get_batch_upload_context(batch_id=batch_id)
        tenant_id = batch_ctx["batch"]["tenant_id"]
        
        await self.backpressure.enforce_limit(tenant_id=tenant_id)
        
        result = await self.batch.add_file_to_batch(batch_id, file, metadata)
        
        # Add queue snapshot to result for UI feedback
        result["queue"] = await self.backpressure.get_pending_snapshot(tenant_id=tenant_id)
        return result

    async def seal_batch(self, batch_id: str) -> Dict[str, Any]:
        return await self.batch.seal_batch(batch_id)

    async def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        status_data = await self.query_service.get_batch_status_data(batch_id=str(batch_id))
        batch = status_data["batch"]
        docs = status_data.get("documents") or []
        events = status_data.get("events") or []

        # 1. Attach stages to docs
        latest_event_by_doc = {}
        for ev in events:
            sid = str(ev.get("source_document_id") or "")
            if sid and sid not in latest_event_by_doc:
                latest_event_by_doc[sid] = ev

        stage_counts = {}
        for doc in docs:
            doc_id = str(doc.get("id") or "")
            latest_ev = latest_event_by_doc.get(doc_id)
            
            if not latest_ev:
                status = str(doc.get("status") or "").lower()
                stage = "QUEUED" if status in {"queued", "pending"} else "OTHER"
            else:
                stage = self.observability.infer_worker_stage(latest_ev.get("message", ""))
            
            doc["worker_stage"] = stage
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        # 2. Delegate components
        visual_accounting = self.observability.calculate_visual_accounting(docs)
        queue_snapshot = await self.backpressure.get_pending_snapshot(batch.get("tenant_id"))
        
        observability = self.observability.build_observability_projection(
            batch=batch,
            docs=docs,
            events=events,
            stage_counts=stage_counts,
            queue_snapshot=queue_snapshot
        )

        return {
            "batch": batch,
            "documents": docs,
            "visual_accounting": visual_accounting,
            "worker_progress": {"stage_counts": stage_counts},
            "observability": observability,
        }

    async def get_batch_progress(self, batch_id: str) -> Dict[str, Any]:
        status = await self.get_batch_status(batch_id=batch_id)
        return {
            "batch": status["batch"],
            "observability": status["observability"],
            "worker_progress": status["worker_progress"],
            "visual_accounting": status["visual_accounting"],
            "documents_count": len(status.get("documents") or []),
        }

    async def get_batch_events(self, batch_id: str, cursor: str | None = None, limit: int = 100) -> Dict[str, Any]:
        payload = await self.query_service.get_batch_events(batch_id=str(batch_id), cursor=cursor, limit=limit)
        items = payload.get("items") or []
        for item in items:
            item["stage"] = self.observability.infer_worker_stage(item.get("message", ""))
        return payload

    async def list_active_batches(self, tenant_id: str, limit: int = 10) -> Dict[str, Any]:
        rows = await self.query_service.list_active_batches(
            tenant_id=str(tenant_id), limit=int(limit)
        )
        items: list[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            batch_id = str(row.get("id") or "").strip()
            if not batch_id:
                continue
            try:
                progress = await self.get_batch_progress(batch_id=batch_id)
                items.append(progress)
            except Exception as exc:
                logger.warning("active_batch_progress_failed", batch_id=batch_id, error=str(exc))
                items.append(
                    {
                        "batch": row,
                        "observability": {},
                        "worker_progress": {},
                        "visual_accounting": {},
                    }
                )
        return {"items": items}
