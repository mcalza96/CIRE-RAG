import asyncio
from typing import Any, Dict

import structlog

from app.workflows.ingestion.processor import DocumentProcessor
from app.infrastructure.background_jobs.tenant_concurrency_manager import (
    AlreadyProcessingError,
    TenantConcurrencyManager,
)
from app.infrastructure.background_jobs.job_store import SupabaseJobStore
from app.workflows.ingestion.job_processor import SourceDocumentJobProcessor

logger = structlog.get_logger(__name__)


class WorkerJobDispatcher:
    def __init__(
        self,
        *,
        job_store: SupabaseJobStore,
        concurrency_manager: TenantConcurrencyManager,
        processor: DocumentProcessor,
        global_semaphore: asyncio.Semaphore,
        enrichment_semaphore: asyncio.Semaphore,
        max_source_lookup_requeues: int,
    ):
        self.job_store = job_store
        self.concurrency_manager = concurrency_manager
        self.processor = processor
        self.global_semaphore = global_semaphore
        self.enrichment_semaphore = enrichment_semaphore
        self.job_processor = SourceDocumentJobProcessor(
            job_store=job_store,
            max_source_lookup_requeues=max_source_lookup_requeues,
        )

    async def handle_ingestion(self, job: Dict[str, Any]) -> Dict[str, Any]:
        source_doc_id, _, record = await self.job_processor.prepare_source_record(
            job,
            retry_on_transient=True,
        )
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        try:
            async with self.concurrency_manager.tenant_slot(
                record=record,
                doc_id=source_doc_id,
                global_semaphore=self.global_semaphore,
            ) as tenant_key:
                try:
                    await self._emit_runtime_metrics(tenant_key, "start", source_doc_id)
                    await self.processor.process(record)
                    await self.job_store.update_batch_progress(record, success=True)
                    return {
                        "ok": True,
                        "source_document_id": source_doc_id,
                        "status": record.get("status"),
                    }
                except Exception:
                    await self.job_store.update_batch_progress(record, success=False)
                    raise
                finally:
                    await self._emit_runtime_metrics(tenant_key, "finish", source_doc_id)
        except AlreadyProcessingError:
            logger.debug("document_already_processing", doc_id=source_doc_id)
            return {"ok": False, "reason": "already_processing"}

    async def handle_enrichment(self, job: Dict[str, Any]) -> Dict[str, Any]:
        source_doc_id, payload, record = await self.job_processor.prepare_source_record(
            job,
            retry_on_transient=False,
        )
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        async with self.enrichment_semaphore:
            return await self.processor.post_ingestion_service.run_deferred_enrichment(
                doc_id=source_doc_id,
                tenant_id=record.get("institution_id"),
                collection_id=payload.get("collection_id") or record.get("collection_id"),
                include_visual=bool(payload.get("include_visual", False)),
                include_graph=bool(payload.get("include_graph", True)),
                include_raptor=bool(payload.get("include_raptor", True)),
            )

    async def _emit_runtime_metrics(self, tenant_id: str, trigger: str, doc_id: str):
        active = await self.concurrency_manager.get_active_jobs_count(tenant_id)
        logger.info(
            "worker_runtime_metrics",
            tenant_id=tenant_id,
            active_jobs=active,
            trigger=trigger,
            doc_id=doc_id,
        )
