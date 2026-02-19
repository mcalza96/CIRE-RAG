import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

from app.core.settings import settings
from app.domain.repositories.source_repository import ISourceRepository
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.knowledge.clustering_service import ClusteringService

logger = logging.getLogger(__name__)


async def rebuild_community_graph_task(tenant_id: str) -> Dict[str, Any]:
    """
    Offline task: rebuild GraphRAG communities for one tenant.
    Safe to call from scheduler, worker hooks, or ad-hoc scripts.
    """
    try:
        tenant_uuid = UUID(str(tenant_id))
    except Exception as exc:
        logger.error("Invalid tenant_id for community rebuild: %s | error=%s", tenant_id, exc)
        return {"ok": False, "reason": "invalid_tenant_id", "tenant_id": tenant_id}

    try:
        service = ClusteringService()
        result = await service.rebuild_communities(tenant_uuid)

        if result.get("communities_detected", 0) == 0:
            logger.warning("Community rebuild skipped (empty graph) tenant=%s", tenant_uuid)

        payload = {"ok": True, "tenant_id": str(tenant_uuid), **result}
        logger.info("Community rebuild completed: %s", payload)
        return payload
    except Exception as exc:
        logger.error("Community rebuild failed tenant=%s error=%s", tenant_uuid, exc, exc_info=True)
        return {
            "ok": False,
            "tenant_id": str(tenant_uuid),
            "reason": "execution_error",
            "error": str(exc),
        }


class CommunityScheduler:
    def __init__(self, source_repository: ISourceRepository):
        self.source_repository = source_repository
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

    async def audit_run(self, tenant_id: str, payload: Dict[str, Any], status: str) -> None:
        doc_id = await self._pick_audit_document_id(tenant_id)
        if not doc_id:
            logger.warning(
                "Skipping ingestion_events audit for tenant=%s (no source document found)",
                tenant_id,
            )
            return

        message = (
            f"Community rebuild tenant={tenant_id}: "
            f"ok={payload.get('ok', False)}, "
            f"detected={payload.get('communities_detected', 0)}, "
            f"persisted={payload.get('communities_persisted', 0)}"
        )
        await self.source_repository.log_event(
            doc_id=doc_id,
            message=message,
            status=status,
            tenant_id=tenant_id,
            metadata={
                "phase": "community_rebuild",
                "tenant_id": tenant_id,
                "run_at": datetime.now(timezone.utc).isoformat(),
                "result": payload,
            },
        )

    async def _pick_audit_document_id(self, tenant_id: str) -> str | None:
        client = await self.get_client()
        try:
            response = (
                await client.table("source_documents")
                .select("id")
                .eq("institution_id", tenant_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                return None
            return str(rows[0].get("id"))
        except Exception as exc:
            logger.warning("Could not resolve audit document for tenant=%s: %s", tenant_id, exc)
            return None
