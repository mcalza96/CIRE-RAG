from __future__ import annotations

from typing import Any, Protocol


class IAtomicRetrievalRepository(Protocol):
    async def retrieve_hybrid_optimized(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def search_vectors_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def search_fts_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def list_source_documents(
        self,
        *,
        tenant_id: str | None,
        is_global: bool,
        collection_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...
