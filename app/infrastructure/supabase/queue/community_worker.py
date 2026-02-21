import asyncio
import structlog
from typing import Callable, Dict, Any, Optional

from app.infrastructure.supabase.queue.job_store import SupabaseJobStore
from app.infrastructure.queue.base_worker import BaseWorkerProcessor
from app.domain.use_cases.community.rebuild_communities import RebuildCommunitiesUseCase
from app.workflows.ingestion.contracts import (
    JobLoopProcessorProtocol,
    WorkerJobStoreProtocol,
)
from app.workflows.ingestion.job_processor import TenantScopedJobProcessor

logger = structlog.get_logger(__name__)


    def __init__(
        self,
        job_store: Optional[WorkerJobStoreProtocol] = None,
        use_case: Optional[RebuildCommunitiesUseCase] = None,
        processor_factory: Optional[
            Callable[[WorkerJobStoreProtocol, int], JobLoopProcessorProtocol]
        ] = None,
    ):
        self.job_store = job_store or SupabaseJobStore()
        self.use_case = use_case or RebuildCommunitiesUseCase()
        self.job_processor = TenantScopedJobProcessor()
        self.job_type = "community_rebuild"
        self.processor_factory = processor_factory or (
            lambda store, poller_id: BaseWorkerProcessor(store, poller_id=poller_id)
        )

    async def handle_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id, tenant_id, _ = self.job_processor.prepare_tenant_job(job)

        logger.info("community_rebuild_worker_task_start", job_id=job_id, tenant_id=tenant_id)

        try:
            return await self.use_case.execute(tenant_id=tenant_id)
        except Exception as exc:
            logger.error(
                "community_rebuild_worker_task_failed", job_id=job_id, tenant_id=tenant_id, error=str(exc)
            )
            raise

    async def start(self):
        logger.info("starting_community_worker")
        processor = self.processor_factory(self.job_store, 1)
        await processor.run_job_loop(self.job_type, self.handle_job, poll_interval=5)


if __name__ == "__main__":
    worker = CommunityWorker()
    asyncio.run(worker.start())
