import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict

import structlog

from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase
from app.infrastructure.concurrency.tenant_concurrency_manager import TenantConcurrencyManager
from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.workflows.ingestion.job_processor import SourceDocumentJobProcessor

logger = structlog.get_logger(__name__)


class WorkerJobDispatcher:
    def __init__(
        self,
        *,
        job_store: SupabaseJobStore,
        concurrency_manager: TenantConcurrencyManager,
        process_use_case: ProcessDocumentWorkerUseCase,
        global_semaphore: asyncio.Semaphore,
        enrichment_semaphore: asyncio.Semaphore,
        max_source_lookup_requeues: int,
    ):
        self.job_store = job_store
        self.concurrency_manager = concurrency_manager
        self.process_use_case = process_use_case
        self.global_semaphore = global_semaphore
        self.enrichment_semaphore = enrichment_semaphore
        self.job_processor = SourceDocumentJobProcessor(
            job_store=job_store,
            max_source_lookup_requeues=max_source_lookup_requeues,
        )
        self._already_processing_error = "already_processing"

    async def handle_ingestion(self, job: Dict[str, Any]) -> Dict[str, Any]:
        source_doc_id, _, record = await self.job_processor.prepare_source_record(
            job,
            retry_on_transient=True,
        )
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        try:
            async with self._tenant_guard(record=record, source_doc_id=source_doc_id):
                try:
                    await self.process_use_case.execute(record)
                    await self.job_store.update_batch_progress(record, success=True)
                    return {
                        "ok": True,
                        "source_document_id": source_doc_id,
                        "status": record.get("status"),
                    }
                except Exception:
                    await self.job_store.update_batch_progress(record, success=False)
                    raise
        except RuntimeError as exc:
            if str(exc) == self._already_processing_error:
                return {"ok": False, "reason": "already_processing"}
            raise

    async def handle_enrichment(self, job: Dict[str, Any]) -> Dict[str, Any]:
        source_doc_id, payload, record = await self.job_processor.prepare_source_record(
            job,
            retry_on_transient=False,
        )
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        async with self.enrichment_semaphore:
            return await self.process_use_case.post_ingestion_service.run_deferred_enrichment(
                doc_id=source_doc_id,
                tenant_id=record.get("institution_id"),
                collection_id=payload.get("collection_id") or record.get("collection_id"),
                include_visual=bool(payload.get("include_visual", False)),
                include_graph=bool(payload.get("include_graph", True)),
                include_raptor=bool(payload.get("include_raptor", True)),
            )

    @asynccontextmanager
    async def _tenant_guard(self, *, record: Dict[str, Any], source_doc_id: str):
        tenant_key = self.concurrency_manager.resolve_tenant_key(record)
        if not await self.concurrency_manager.try_acquire_doc_lock(source_doc_id):
            logger.debug("document_already_processing", doc_id=source_doc_id)
            raise RuntimeError(self._already_processing_error)

        try:
            async with self.global_semaphore:
                tenant_sem = await self.concurrency_manager.get_semaphore(tenant_key)
                async with tenant_sem:
                    await self.concurrency_manager.increment_active_jobs(tenant_key)
                    await self._emit_runtime_metrics(tenant_key, "start", source_doc_id)
                    try:
                        yield
                    finally:
                        await self.concurrency_manager.decrement_active_jobs(tenant_key)
                        await self._emit_runtime_metrics(tenant_key, "finish", source_doc_id)
        finally:
            await self.concurrency_manager.release_doc_lock(source_doc_id)

    async def _emit_runtime_metrics(self, tenant_id: str, trigger: str, doc_id: str):
        active = await self.concurrency_manager.get_active_jobs_count(tenant_id)
        logger.info(
            "worker_runtime_metrics",
            tenant_id=tenant_id,
            active_jobs=active,
            trigger=trigger,
            doc_id=doc_id,
        )
