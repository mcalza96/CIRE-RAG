import structlog
from typing import Optional, Dict, Any, List
from fastapi import UploadFile

from app.infrastructure.supabase.repositories.taxonomy_repository import TaxonomyRepository
from app.infrastructure.supabase.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.supabase.queries.ingestion_query_service import ManualIngestionQueryService
from app.infrastructure.observability.ingestion.backpressure import IngestionBackpressureService
from app.infrastructure.observability.ingestion.ingestion_tracer import IngestionObservabilityService
from app.infrastructure.state_management.batch_manager import IngestionBatchService
from app.domain.ingestion.types import IngestionStatus

logger = structlog.get_logger(__name__)

class BatchOrchestrator:
    """
    Service for managing ingestion batches and observability.
    Consolidates functionality for creating, adding files, sealing, and monitoring batches.
    """
    def __init__(
        self,
        query_service: Optional[ManualIngestionQueryService] = None,
        taxonomy_manager: Optional[TaxonomyRepository] = None,
        source_repo: Optional[SupabaseSourceRepository] = None,
        backpressure_service: Optional[IngestionBackpressureService] = None,
        observability_service: Optional[IngestionObservabilityService] = None,
        batch_service: Optional[IngestionBatchService] = None
    ):
        self.query_service = query_service or ManualIngestionQueryService()
        self.taxonomy_manager = taxonomy_manager or TaxonomyRepository()
        self.source_repo = source_repo or SupabaseSourceRepository()
        
        self.backpressure = backpressure_service or IngestionBackpressureService(self.query_service)
        self.observability = observability_service or IngestionObservabilityService(self.query_service)
        self.batch_manager = batch_service or IngestionBatchService(self.query_service, self.taxonomy_manager)

    async def create_batch(self, *args, **kwargs) -> Dict[str, Any]:
        return await self.batch_manager.create_batch(*args, **kwargs)

    async def add_file_to_batch(self, batch_id: str, file: UploadFile, metadata: Optional[str] = None) -> Dict[str, Any]:
        batch_ctx = await self.query_service.get_batch_upload_context(batch_id=batch_id)
        tenant_id = batch_ctx["batch"]["tenant_id"]
        
        await self.backpressure.enforce_limit(tenant_id=tenant_id)
        result = await self.batch_manager.add_file_to_batch(batch_id, file, metadata)
        result["queue"] = await self.backpressure.get_pending_snapshot(tenant_id=tenant_id)
        return result

    async def seal_batch(self, batch_id: str) -> Dict[str, Any]:
        return await self.batch_manager.seal_batch(batch_id)

    async def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        status_data = await self.query_service.get_batch_status_data(batch_id=str(batch_id))
        batch = status_data["batch"]
        docs = status_data.get("documents") or []
        events = status_data.get("events") or []

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

    async def list_active_batches(self, tenant_id: str, limit: int = 10) -> Dict[str, Any]:
        rows = await self.query_service.list_active_batches(tenant_id=str(tenant_id), limit=int(limit))
        items: list[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict): continue
            batch_id = str(row.get("id") or "").strip()
            if not batch_id: continue
            try:
                status = await self.get_batch_status(batch_id=batch_id)
                items.append({
                    "batch": status["batch"],
                    "observability": status["observability"],
                    "worker_progress": status["worker_progress"],
                    "visual_accounting": status["visual_accounting"],
                    "documents_count": len(status.get("documents") or []),
                })
            except Exception as exc:
                logger.warning("active_batch_progress_failed", batch_id=batch_id, error=str(exc))
                items.append({"batch": row, "observability": {}, "worker_progress": {}, "visual_accounting": {}})
        return {"items": items}

    async def retry_ingestion(self, doc_id: str) -> str:
        doc = await self.source_repo.get_by_id(doc_id)
        if not doc: raise ValueError(f"Document {doc_id} not found.")
        current_metadata = doc.get("metadata", {}) or {}
        current_metadata["retry_count"] = current_metadata.get("retry_count", 0) + 1
        await self.source_repo.update_status_and_metadata(doc_id, IngestionStatus.QUEUED.value, current_metadata)
        return doc_id
