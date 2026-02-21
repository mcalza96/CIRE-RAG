from __future__ import annotations

from typing import Any

from app.infrastructure.supabase.client import get_async_supabase_client


class SupabaseAtomicRetrievalRepository:
    def __init__(self, supabase_client: Any | None = None):
        self._supabase_client = supabase_client

    async def _get_client(self) -> Any:
        if self._supabase_client is not None:
            return self._supabase_client
        self._supabase_client = await get_async_supabase_client()
        return self._supabase_client

    async def retrieve_hybrid_optimized(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc("retrieve_hybrid_optimized", payload).execute()
        data = response.data if isinstance(response.data, list) else []
        return [row for row in data if isinstance(row, dict)]

    async def search_vectors_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc("search_vectors_only", payload).execute()
        data = response.data if isinstance(response.data, list) else []
        return [row for row in data if isinstance(row, dict)]

    async def search_fts_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc("search_fts_only", payload).execute()
        data = response.data if isinstance(response.data, list) else []
        return [row for row in data if isinstance(row, dict)]
