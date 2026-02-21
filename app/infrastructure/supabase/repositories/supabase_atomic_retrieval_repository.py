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

    async def fetch_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """Hydrate full content_chunks records by their IDs.

        Returns normalized rows compatible with retrieve_hybrid_optimized output.
        """
        if not chunk_ids:
            return []

        client = await self._get_client()
        response = (
            await client.table("content_chunks")
            .select("id,content,metadata,source_id")
            .in_("id", chunk_ids)
            .execute()
        )
        data = response.data if isinstance(response.data, list) else []
        normalized: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": 0.0,  # Will be set by caller with graph similarity
                    "score": 0.0,
                    "source_layer": "graph_grounded",
                    "source_type": "content_chunk",
                    "source_id": str(row.get("source_id") or metadata.get("source_id") or ""),
                }
            )
        return normalized
