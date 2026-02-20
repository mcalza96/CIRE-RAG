import asyncio
import structlog
from typing import Optional

from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase
from app.core.settings import settings
from app.domain.policies.ingestion_policy import IngestionPolicy
from app.infrastructure.adapters.supabase_metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.container import CognitiveContainer
from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.infrastructure.concurrency.tenant_concurrency_manager import TenantConcurrencyManager
from app.infrastructure.queue.base_worker import BaseWorkerProcessor
from app.services.database.taxonomy_manager import TaxonomyManager
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.workflows.ingestion.job_dispatcher import WorkerJobDispatcher
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
        concurrency_manager: Optional[TenantConcurrencyManager] = None,
        process_use_case: Optional[ProcessDocumentWorkerUseCase] = None,
        community_scheduler: Optional[CommunityScheduler] = None,
        dispatcher: Optional[IngestionDispatcher] = None,
        policy: Optional[IngestionPolicy] = None,
    ):
        self.is_running = True
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

        self.dispatcher = dispatcher or IngestionDispatcher()
        self.policy = policy or IngestionPolicy()
        self.process_use_case = process_use_case

        resolved_container = container
        if self.process_use_case is None or community_scheduler is None:
            resolved_container = resolved_container or CognitiveContainer()

        if self.process_use_case is None:
            from app.infrastructure.repositories.supabase_raptor_repository import (
                SupabaseRaptorRepository,
            )
            from app.services.knowledge.raptor_processor import RaptorProcessor

            if resolved_container is None:
                resolved_container = CognitiveContainer()

            raptor_repo = SupabaseRaptorRepository()
            raptor_processor = RaptorProcessor(repository=raptor_repo)
            self.process_use_case = ProcessDocumentWorkerUseCase(
                repository=resolved_container.source_repository,
                content_repo=resolved_container.content_repository,
                storage_service=resolved_container.storage_service,
                dispatcher=self.dispatcher,
                taxonomy_manager=TaxonomyManager(),
                metadata_adapter=SupabaseMetadataAdapter(),
                policy=self.policy,
                raptor_processor=raptor_processor,
                raptor_repo=raptor_repo,
                download_service=resolved_container.download_service,
                state_manager=resolved_container.state_manager,
            )

        if resolved_container is None:
            resolved_container = CognitiveContainer()

        self.community_scheduler = community_scheduler or CommunityScheduler(
            source_repository=resolved_container.source_repository
        )
        self.job_dispatcher = WorkerJobDispatcher(
            job_store=self.job_store,
            concurrency_manager=self.concurrency_manager,
            process_use_case=self.process_use_case,
            global_semaphore=self._global_semaphore,
            enrichment_semaphore=self._enrichment_semaphore,
            max_source_lookup_requeues=self.max_source_lookup_requeues,
        )

    async def start(self):
        await self.job_store.get_client()
        logger.info(
            "starting_ingestion_worker",
            concurrency=self.worker_concurrency,
            per_tenant=self.worker_per_tenant_concurrency,
        )

        # Poller tasks
        poller_tasks = []
        for i in range(self.worker_concurrency):
            p = BaseWorkerProcessor(self.job_store, poller_id=i + 1)
            poller_tasks.append(
                asyncio.create_task(
                    p.run_job_loop(
                        "ingest_document",
                        self.job_dispatcher.handle_ingestion,
                        self.worker_poll_interval,
                    )
                )
            )

        for i in range(self.enrichment_concurrency):
            p = BaseWorkerProcessor(self.job_store, poller_id=i + 1)
            poller_tasks.append(
                asyncio.create_task(
                    p.run_job_loop(
                        "enrich_document",
                        self.job_dispatcher.handle_enrichment,
                        self.worker_poll_interval,
                    )
                )
            )

        async def scheduler_loop():
            while self.is_running:
                await self.community_scheduler.tick()
                await asyncio.sleep(1)

        scheduler_task = asyncio.create_task(scheduler_loop())

        try:
            await asyncio.gather(*poller_tasks, scheduler_task)
        finally:
            self.is_running = False
            for t in poller_tasks:
                t.cancel()
            scheduler_task.cancel()


if __name__ == "__main__":
    worker = IngestionWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("stopping_worker")
