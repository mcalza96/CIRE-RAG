import asyncio
import structlog
from uuid import UUID
from typing import Dict, Any, Optional

from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.infrastructure.queue.base_worker import BaseWorkerProcessor
from app.infrastructure.repositories.community_job_repository import CommunityJobRepository
from app.services.knowledge.clustering_service import ClusteringService
from app.workflows.ingestion.job_processor import TenantScopedJobProcessor

logger = structlog.get_logger(__name__)


class CommunityWorker:
    def __init__(
        self,
        job_store: Optional[SupabaseJobStore] = None,
        repository: Optional[CommunityJobRepository] = None,
        clustering_service: Optional[ClusteringService] = None,
    ):
        self.job_store = job_store or SupabaseJobStore()
        self.repository = repository or CommunityJobRepository()
        self.clustering_service = clustering_service or ClusteringService()
        self.job_processor = TenantScopedJobProcessor()
        self.job_type = "community_rebuild"

    async def handle_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id, tenant_id, _ = self.job_processor.prepare_tenant_job(job)

        logger.info("community_rebuild_start", job_id=job_id, tenant_id=tenant_id)

        try:
            result = await self.clustering_service.rebuild_communities(tenant_id=UUID(tenant_id))
            payload = {"ok": True, "tenant_id": tenant_id, **result}

            # Log event
            doc_id = await self.repository.get_latest_document_id(tenant_id)
            if doc_id:
                msg = (
                    f"Community rebuild tenant={tenant_id}: ok=True "
                    f"detected={result.get('communities_detected', 0)} "
                    f"persisted={result.get('communities_persisted', 0)}"
                )
                await self.repository.create_ingestion_event(
                    doc_id, tenant_id, msg, "SUCCESS", payload
                )

            return payload

        except Exception as exc:
            logger.error(
                "community_rebuild_failed", job_id=job_id, tenant_id=tenant_id, error=str(exc)
            )
            payload = {"ok": False, "tenant_id": tenant_id, "error": str(exc)}

            doc_id = await self.repository.get_latest_document_id(tenant_id)
            if doc_id:
                await self.repository.create_ingestion_event(
                    doc_id, tenant_id, f"Community rebuild failed: {str(exc)}", "WARNING", payload
                )
            raise

    async def start(self):
        logger.info("starting_community_worker")
        processor = BaseWorkerProcessor(self.job_store, poller_id=1)
        await processor.run_job_loop(self.job_type, self.handle_job, poll_interval=5)


if __name__ == "__main__":
    worker = CommunityWorker()
    asyncio.run(worker.start())
