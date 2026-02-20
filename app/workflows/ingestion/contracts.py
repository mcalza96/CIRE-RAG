from typing import Any, Awaitable, Callable, Dict, Optional, Protocol
from uuid import UUID


class WorkerJobStoreProtocol(Protocol):
    async def get_client(self) -> Any: ...


class JobLoopProcessorProtocol(Protocol):
    async def run_job_loop(
        self,
        job_type: str,
        handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        poll_interval: int = 2,
    ) -> None: ...


class CommunityJobRepositoryProtocol(Protocol):
    async def get_latest_document_id(self, tenant_id: str) -> Optional[str]: ...

    async def create_ingestion_event(
        self,
        doc_id: str,
        tenant_id: str,
        message: str,
        status: str,
        result: Dict[str, Any],
    ) -> None: ...


class CommunityClusteringServiceProtocol(Protocol):
    async def rebuild_communities(self, tenant_id: UUID) -> Dict[str, Any]: ...
