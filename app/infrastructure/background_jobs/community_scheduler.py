import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from app.infrastructure.settings import settings
from app.infrastructure.supabase.client import get_async_supabase_client
from app.workflows.community.rebuild_communities import RebuildCommunitiesUseCase

import structlog

logger = structlog.get_logger(__name__)


# Logic moved to RebuildCommunitiesUseCase


class CommunityScheduler:
    def __init__(self, use_case: RebuildCommunitiesUseCase | None = None):
        self.use_case = use_case or RebuildCommunitiesUseCase()
        self.enabled = settings.COMMUNITY_REBUILD_ENABLED
        self.interval_seconds = settings.COMMUNITY_REBUILD_INTERVAL_SECONDS
        self.explicit_tenants = [
            token.strip()
            for token in settings.COMMUNITY_REBUILD_TENANTS.split(",")
            if token.strip()
        ]
        self._last_rebuild_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._client = None

    async def get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client

    async def tick(self) -> None:
        if not self.enabled:
            return

        now = datetime.now(timezone.utc)
        if self._last_rebuild_at is not None:
            next_run = self._last_rebuild_at + timedelta(seconds=self.interval_seconds)
            if now < next_run:
                return

        if self._lock.locked():
            return

        async with self._lock:
            self._last_rebuild_at = now
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error("Community scheduler cycle failed: %s", exc, exc_info=True)

    async def _run_cycle(self) -> None:
        tenant_ids = await self._resolve_tenant_ids()
        if not tenant_ids:
            logger.info("Community scheduler: no tenants to rebuild")
            return

        logger.info("Community scheduler: starting cycle tenants=%s", len(tenant_ids))
        for tenant_id in tenant_ids:
            await self._enqueue_job(tenant_id)

    async def _resolve_tenant_ids(self) -> List[str]:
        if self.explicit_tenants:
            return self.explicit_tenants

        client = await self.get_client()
        try:
            response = (
                await client.table("knowledge_entities").select("tenant_id").limit(10000).execute()
            )
            rows = response.data or []
            tenant_ids = sorted({str(row.get("tenant_id")) for row in rows if row.get("tenant_id")})
            return tenant_ids
        except Exception as exc:
            logger.warning("Could not auto-resolve tenants for community rebuild: %s", exc)
            return []

    async def _enqueue_job(self, tenant_id: str) -> None:
        client = await self.get_client()
        try:
            existing = (
                await client.table("job_queue")
                .select("id,status")
                .eq("job_type", "community_rebuild")
                .eq("tenant_id", tenant_id)
                .in_("status", ["pending", "processing"])
                .limit(1)
                .execute()
            )

            if existing.data:
                logger.info(
                    "community_rebuild_job_exists",
                    tenant_id=tenant_id,
                    job_id=existing.data[0].get("id"),
                )
                return

            inserted = (
                await client.table("job_queue")
                .insert(
                    {
                        "job_type": "community_rebuild",
                        "tenant_id": tenant_id,
                        "payload": {"tenant_id": tenant_id, "scheduled_by": "ingestion_worker"},
                    }
                )
                .execute()
            )

            job_id = (inserted.data or [{}])[0].get("id")
            logger.info("community_rebuild_job_enqueued", tenant_id=tenant_id, job_id=job_id)
        except Exception as exc:
            logger.error(
                "community_rebuild_job_enqueue_failed", tenant_id=tenant_id, error=str(exc)
            )

# Audit logic now handled by UseCase within _enqueue_job or directly if running in-process.
