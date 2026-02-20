import math
import structlog
from typing import Optional, Dict

from app.infrastructure.settings import settings
from app.domain.types.ingestion_status import IngestionStatus
from app.infrastructure.services.manual_ingestion_query_service import ManualIngestionQueryService

logger = structlog.get_logger(__name__)

class IngestionBackpressureService:
    """
    Service responsible for calculating ingestion queue pressure, 
    estimating wait times (ETAs), and enforcing tenant-specific limits.
    """
    def __init__(self, query_service: Optional[ManualIngestionQueryService] = None):
        self.query_service = query_service or ManualIngestionQueryService()

    async def get_pending_snapshot(self, tenant_id: Optional[str]) -> Dict[str, Optional[int]]:
        """
        Calculates current queue depth and estimated wait time for a tenant.
        """
        max_pending = int(getattr(settings, "INGESTION_MAX_PENDING_PER_TENANT", 0) or 0)
        
        if not tenant_id:
            return {
                "queue_depth": 0,
                "max_pending": max_pending if max_pending > 0 else None,
                "estimated_wait_seconds": 0,
            }

        # We query one more than max_pending to detect overflows efficiently
        limit = max_pending + 1 if max_pending > 0 else 1000
        
        pending_count = await self.query_service.count_pending_documents(
            tenant_id=str(tenant_id),
            limit=limit,
            statuses=[
                IngestionStatus.QUEUED.value,
                IngestionStatus.PENDING.value,
                IngestionStatus.PENDING_INGESTION.value,
                IngestionStatus.PROCESSING.value,
                IngestionStatus.PROCESSING_V2.value,
            ],
        )

        # Performance assumptions for ETA calculation
        docs_per_minute_per_worker = max(1, int(getattr(settings, "INGESTION_DOCS_PER_MINUTE_PER_WORKER", 2) or 2))
        worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 1) or 1))
        throughput_per_minute = docs_per_minute_per_worker * worker_concurrency
        
        estimated_wait_seconds = (
            int(math.ceil((pending_count / throughput_per_minute) * 60)) if pending_count > 0 else 0
        )

        return {
            "queue_depth": pending_count,
            "max_pending": max_pending if max_pending > 0 else None,
            "estimated_wait_seconds": estimated_wait_seconds,
        }

    async def enforce_limit(self, tenant_id: Optional[str]) -> Dict[str, Optional[int]]:
        """
        Verifies if the tenant has exceeded their pending document limit.
        Raises ValueError if backpressure is too high.
        """
        if not tenant_id:
            return await self.get_pending_snapshot(tenant_id=None)

        snapshot = await self.get_pending_snapshot(tenant_id=tenant_id)
        max_pending = int(snapshot.get("max_pending") or 0)
        
        if max_pending <= 0:
            return snapshot

        pending_count = int(snapshot.get("queue_depth") or 0)
        if pending_count >= max_pending:
            logger.warning("ingestion_backpressure_limit_reached", 
                           tenant_id=tenant_id, 
                           pending=pending_count, 
                           max=max_pending)
            raise ValueError(
                "INGESTION_BACKPRESSURE "
                f"tenant={tenant_id} pending={pending_count} max={max_pending} "
                f"eta_seconds={int(snapshot.get('estimated_wait_seconds') or 0)}"
            )

        return snapshot
