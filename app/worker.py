import asyncio
import time
import structlog
from typing import Any, Dict, Optional, List

from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase
from app.core.observability.ingestion_logging import compact_error, emit_event
from app.core.settings import settings
from app.domain.policies.ingestion_policy import IngestionPolicy
from app.infrastructure.adapters.supabase_metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.container import CognitiveContainer
from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.infrastructure.concurrency.tenant_concurrency_manager import TenantConcurrencyManager
from app.infrastructure.queue.base_worker import BaseWorkerProcessor
from app.services.database.taxonomy_manager import TaxonomyManager
from app.workflows.ingestion.dispatcher import IngestionDispatcher
import app.workflows.ingestion.strategies  # Trigger strategy registration

from app.schedules.community_scheduler import (
    CommunityScheduler,
)

logger = structlog.get_logger(__name__)

class IngestionWorker:
    """
    Worker class for handling document ingestion and enrichment jobs.
    Refactored to use modular components for concurrency and job processing.
    """
    def __init__(
        self,
        container: Optional[CognitiveContainer] = None,
        job_store: Optional[SupabaseJobStore] = None,
        concurrency_manager: Optional[TenantConcurrencyManager] = None
    ):
        self.is_running = True
        self.container = container or CognitiveContainer()
        self.job_store = job_store or SupabaseJobStore()
        
        # Load concurrency settings
        self.worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 3)))
        self.worker_per_tenant_concurrency = max(
            1,
            int(getattr(settings, "WORKER_PER_TENANT_CONCURRENCY", 1)),
        )
        self.worker_poll_interval = max(
            1,
            int(getattr(settings, "WORKER_POLL_INTERVAL_SECONDS", 2)),
        )
        self.enrichment_concurrency = max(
            1,
            int(getattr(settings, "ENRICHMENT_WORKER_CONCURRENCY", 2)),
        )
        self.max_source_lookup_requeues = max(
            0,
            int(getattr(settings, "WORKER_SOURCE_LOOKUP_MAX_REQUEUES", 3) or 3),
        )

        self.concurrency_manager = concurrency_manager or TenantConcurrencyManager(
            per_tenant_limit=self.worker_per_tenant_concurrency
        )
        self._global_semaphore = asyncio.Semaphore(self.worker_concurrency)
        self._enrichment_semaphore = asyncio.Semaphore(self.enrichment_concurrency)
        
        # Initialize Dependencies
        from app.infrastructure.repositories.supabase_raptor_repository import SupabaseRaptorRepository
        from app.services.knowledge.raptor_processor import RaptorProcessor

        self.raptor_repo = SupabaseRaptorRepository()
        self.raptor_processor = RaptorProcessor(repository=self.raptor_repo)
        self.dispatcher = IngestionDispatcher()
        self.policy = IngestionPolicy()
        
        self.process_use_case = ProcessDocumentWorkerUseCase(
            repository=self.container.source_repository,
            content_repo=self.container.content_repository,
            storage_service=self.container.storage_service,
            dispatcher=self.dispatcher,
            taxonomy_manager=TaxonomyManager(),
            metadata_adapter=SupabaseMetadataAdapter(),
            policy=self.policy,
            raptor_processor=self.raptor_processor,
            raptor_repo=self.raptor_repo,
            download_service=self.container.download_service,
            state_manager=self.container.state_manager,
        )
        
        self.community_scheduler = CommunityScheduler(source_repository=self.container.source_repository)
        self._source_lookup_requeues: Dict[str, int] = {}

    async def _handle_ingestion(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Specific handler for document ingestion."""
        job_id = job["id"]
        payload = job.get("payload", {})
        source_doc_id = payload.get("source_document_id")
        
        if not source_doc_id:
            raise ValueError("Missing source_document_id in payload")

        # Load Source Document with transient retry logic
        record = await self._load_record_with_retries(job_id, source_doc_id)
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        tenant_key = self.concurrency_manager.resolve_tenant_key(record)
        
        # Apply Concurrency Controls
        if not await self.concurrency_manager.try_acquire_doc_lock(source_doc_id):
            logger.debug("document_already_processing", doc_id=source_doc_id)
            return {"ok": False, "reason": "already_processing"}

        try:
            async with self._global_semaphore:
                tenant_sem = await self.concurrency_manager.get_semaphore(tenant_key)
                async with tenant_sem:
                    await self.concurrency_manager.increment_active_jobs(tenant_key)
                    try:
                        await self._emit_runtime_metrics(tenant_key, "start", source_doc_id)
                        await self.process_use_case.execute(record)
                        await self.job_store.update_batch_progress(record, success=True)
                        return {"ok": True, "source_document_id": source_doc_id, "status": record.get("status")}
                    except Exception as e:
                        await self.job_store.update_batch_progress(record, success=False)
                        raise e
                    finally:
                        await self.concurrency_manager.decrement_active_jobs(tenant_key)
                        await self._emit_runtime_metrics(tenant_key, "finish", source_doc_id)
        finally:
            await self.concurrency_manager.release_doc_lock(source_doc_id)

    async def _handle_enrichment(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Specific handler for document enrichment."""
        job_id = job["id"]
        payload = job.get("payload", {})
        source_doc_id = payload.get("source_document_id")
        
        if not source_doc_id:
            raise ValueError("Missing source_document_id in payload")

        record = await self.job_store.load_source_document(source_doc_id)
        if not record:
            return {"ok": False, "reason": "source_document_not_found"}

        async with self._enrichment_semaphore:
            result = await self.process_use_case.post_ingestion_service.run_deferred_enrichment(
                doc_id=source_doc_id,
                tenant_id=record.get("institution_id"),
                collection_id=payload.get("collection_id") or record.get("collection_id"),
                include_visual=bool(payload.get("include_visual", False)),
                include_graph=bool(payload.get("include_graph", True)),
                include_raptor=bool(payload.get("include_raptor", True)),
            )
            return result

    async def _load_record_with_retries(self, job_id: str, source_doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            record = await self.job_store.load_source_document(source_doc_id)
            self._source_lookup_requeues.pop(job_id, None)
            return record
        except Exception as exc:
            if not self.job_store.is_transient_supabase_transport_error(exc):
                raise exc
            
            attempts = self._source_lookup_requeues.get(job_id, 0) + 1
            self._source_lookup_requeues[job_id] = attempts
            
            if attempts > self.max_source_lookup_requeues:
                self._source_lookup_requeues.pop(job_id, None)
                return None # Mark as failed in caller
            
            # Requeue for retry
            await self.job_store.requeue_job_for_retry(
                job_id=job_id, 
                error_message=f"transient_lookup_attempt_{attempts}: {compact_error(exc)}"
            )
            # We raise a specialized exception or just return None to stop current execution
            # The poller will pick it up again later.
            raise asyncio.CancelledError("Requeued due to transient lookup error")

    async def _emit_runtime_metrics(self, tenant_id: str, trigger: str, doc_id: str):
        active = await self.concurrency_manager.get_active_jobs_count(tenant_id)
        logger.info("worker_runtime_metrics", 
                    tenant_id=tenant_id, 
                    active_jobs=active, 
                    trigger=trigger, 
                    doc_id=doc_id)

    async def start(self):
        await self.job_store.get_client()
        logger.info("starting_ingestion_worker", 
                    concurrency=self.worker_concurrency, 
                    per_tenant=self.worker_per_tenant_concurrency)

        # Poller tasks
        poller_tasks = []
        for i in range(self.worker_concurrency):
            p = BaseWorkerProcessor(self.job_store, poller_id=i+1)
            poller_tasks.append(asyncio.create_task(
                p.run_job_loop("ingest_document", self._handle_ingestion, self.worker_poll_interval)
            ))
            
        for i in range(self.enrichment_concurrency):
            p = BaseWorkerProcessor(self.job_store, poller_id=i+1)
            poller_tasks.append(asyncio.create_task(
                p.run_job_loop("enrich_document", self._handle_enrichment, self.worker_poll_interval)
            ))

        async def scheduler_loop():
            while self.is_running:
                await self.community_scheduler.tick()
                await asyncio.sleep(1)
        
        scheduler_task = asyncio.create_task(scheduler_loop())
        
        try:
            await asyncio.gather(*poller_tasks, scheduler_task)
        finally:
            self.is_running = False
            for t in poller_tasks: t.cancel()
            scheduler_task.cancel()

if __name__ == "__main__":
    worker = IngestionWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("stopping_worker")
