import asyncio
import structlog
from typing import Dict, Set

logger = structlog.get_logger(__name__)

class TenantConcurrencyManager:
    """
    Manages concurrency limits and active job tracking per tenant.
    """
    def __init__(self, per_tenant_limit: int):
        self.per_tenant_limit = per_tenant_limit
        self._tenant_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._tenant_semaphores_lock = asyncio.Lock()
        self._tenant_active_jobs: Dict[str, int] = {}
        self._tenant_active_jobs_lock = asyncio.Lock()
        self._active_doc_ids: Set[str] = set()
        self._active_doc_lock = asyncio.Lock()

    async def get_semaphore(self, tenant_id: str) -> asyncio.Semaphore:
        async with self._tenant_semaphores_lock:
            if tenant_id not in self._tenant_semaphores:
                self._tenant_semaphores[tenant_id] = asyncio.Semaphore(self.per_tenant_limit)
            return self._tenant_semaphores[tenant_id]

    async def try_acquire_doc_lock(self, doc_id: str) -> bool:
        async with self._active_doc_lock:
            if doc_id in self._active_doc_ids:
                return False
            self._active_doc_ids.add(doc_id)
            return True

    async def release_doc_lock(self, doc_id: str):
        async with self._active_doc_lock:
            if doc_id in self._active_doc_ids:
                self._active_doc_ids.remove(doc_id)

    async def increment_active_jobs(self, tenant_id: str):
        async with self._tenant_active_jobs_lock:
            self._tenant_active_jobs[tenant_id] = self._tenant_active_jobs.get(tenant_id, 0) + 1

    async def decrement_active_jobs(self, tenant_id: str):
        async with self._tenant_active_jobs_lock:
            current = self._tenant_active_jobs.get(tenant_id, 0)
            if current <= 1:
                self._tenant_active_jobs.pop(tenant_id, None)
            else:
                self._tenant_active_jobs[tenant_id] = current - 1

    async def get_active_jobs_count(self, tenant_id: str) -> int:
        async with self._tenant_active_jobs_lock:
            return self._tenant_active_jobs.get(tenant_id, 0)

    def resolve_tenant_key(self, record: dict) -> str:
        tenant_id = record.get("institution_id")
        if tenant_id:
            return str(tenant_id)
        metadata = record.get("metadata")
        if isinstance(metadata, dict) and metadata.get("institution_id"):
            return str(metadata.get("institution_id"))
        return "global"
