from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.infrastructure.supabase.client import get_async_supabase_client

logger = structlog.get_logger(__name__)


class SupabaseGraphRetrievalRepository:
    """Data-plane access for graph retrieval strategies."""

    def __init__(self, supabase_client: Any | None = None):
        self._supabase = supabase_client

    async def _get_client(self) -> Any:
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    async def match_exact_entities(
        self, tenant_id: UUID, entity_name: str, limit: int = 6
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = (
            await client.table("knowledge_entities")
            .select("id,name,type,description,embedding")
            .eq("tenant_id", str(tenant_id))
            .ilike("name", entity_name)
            .limit(limit)
            .execute()
        )
        return self._rows(response.data)

    async def match_entities_by_vector_rpc(
        self,
        tenant_id: UUID,
        vector: list[float],
        threshold: float,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc(
            "match_knowledge_entities",
            {
                "query_embedding": vector,
                "p_tenant_id": str(tenant_id),
                "match_count": limit,
                "match_threshold": threshold,
            },
        ).execute()
        return self._rows(response.data)

    async def list_entities_with_embeddings(self, tenant_id: UUID) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = (
            await client.table("knowledge_entities")
            .select("id,name,type,description,embedding")
            .eq("tenant_id", str(tenant_id))
            .execute()
        )
        return self._rows(response.data)

    async def fetch_one_hop_relations(
        self, tenant_id: UUID, anchor_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not anchor_ids:
            return []
        client = await self._get_client()
        anchors_csv = ",".join(anchor_ids)
        relation_filter = f"source_entity_id.in.({anchors_csv}),target_entity_id.in.({anchors_csv})"
        response = (
            await client.table("knowledge_relations")
            .select("id,source_entity_id,target_entity_id,relation_type,description,weight")
            .eq("tenant_id", str(tenant_id))
            .or_(relation_filter)
            .execute()
        )
        return self._rows(response.data)

    async def fetch_entities_by_ids(self, tenant_id: UUID, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        client = await self._get_client()
        response = (
            await client.table("knowledge_entities")
            .select("id,name,type,description")
            .eq("tenant_id", str(tenant_id))
            .in_("id", ids)
            .execute()
        )
        return self._rows(response.data)

    async def match_communities_by_vector_rpc(
        self,
        tenant_id: UUID,
        query_vector: list[float],
        top_k: int,
        level: int = 0,
        threshold: float = 0.25,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc(
            "match_knowledge_communities",
            {
                "query_embedding": query_vector,
                "p_tenant_id": str(tenant_id),
                "p_level": level,
                "match_count": top_k,
                "match_threshold": threshold,
            },
        ).execute()
        return self._rows(response.data)

    async def list_level_communities(self, tenant_id: UUID, level: int = 0) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = (
            await client.table("knowledge_communities")
            .select("id,community_id,summary,members,embedding")
            .eq("tenant_id", str(tenant_id))
            .eq("level", level)
            .execute()
        )
        return self._rows(response.data)

    async def resolve_node_to_chunk_ids(
        self, node_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Resolve knowledge entity IDs → content chunk IDs via knowledge_node_provenance.

        Returns list of dicts with keys: chunk_id, node_id (entity_id).
        """
        if not node_ids:
            return []

        client = await self._get_client()
        try:
            response = (
                await client.table("knowledge_node_provenance")
                .select("entity_id,chunk_id")
                .in_("entity_id", node_ids)
                .execute()
            )
            rows = self._rows(response.data)
            return [
                {
                    "chunk_id": str(row.get("chunk_id") or ""),
                    "node_id": str(row.get("entity_id") or ""),
                }
                for row in rows
                if row.get("chunk_id")
            ]
        except Exception as exc:
            logger.warning("resolve_node_to_chunk_ids_failed", error=str(exc), node_count=len(node_ids))
            return []

    async def search_multi_hop_context(
        self,
        tenant_id: UUID,
        query_vector: list[float],
        match_threshold: float = 0.25,
        limit_count: int = 12,
        max_hops: int = 2,
        decay_factor: float = 0.82,
        filter_node_types: list[str] | None = None,
        filter_relation_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()

        def _normalize_nav_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized: list[dict[str, Any]] = []
            for row in rows:
                path_info = row.get("path_info")
                path_ids: list[str] = []
                if isinstance(path_info, list):
                    for step in path_info:
                        if isinstance(step, dict) and step.get("id"):
                            path_ids.append(str(step.get("id")))

                entity_id = row.get("entity_id") or row.get("id")
                entity_name = row.get("entity_name") or row.get("title")
                entity_type = row.get("entity_type") or row.get("node_type")
                entity_description = row.get("entity_description") or row.get("description")

                normalized.append(
                    {
                        "entity_id": entity_id,
                        "entity_name": entity_name,
                        "entity_type": entity_type,
                        "entity_description": entity_description,
                        "similarity": row.get("similarity"),
                        "hop_depth": row.get("hop_depth"),
                        "path_ids": row.get("path_ids") or path_ids,
                    }
                )
            return normalized

        try:
            response = await client.rpc(
                "search_graph_nav",
                {
                    "query_embedding": query_vector,
                    "p_tenant_id": str(tenant_id),
                    "match_threshold": match_threshold,
                    "limit_count": limit_count,
                    "max_hops": max_hops,
                    "decay_factor": decay_factor,
                    # NOTE: RPC expects `filter_entity_types` (legacy caller used `filter_node_types`).
                    "filter_entity_types": filter_node_types,
                    "filter_relation_types": filter_relation_types,
                },
            ).execute()
            nav_rows = self._rows(response.data)
            if nav_rows:
                return _normalize_nav_rows(nav_rows)
        except Exception as exc:
            logger.info("search_graph_nav_unavailable_fallback", error=str(exc))

        response = await client.rpc(
            "hybrid_multi_hop_search",
            {
                "query_embedding": query_vector,
                "p_tenant_id": str(tenant_id),
                "match_threshold": match_threshold,
                "limit_count": limit_count,
                "max_hops": max_hops,
                "decay_factor": decay_factor,
            },
        ).execute()
        return self._rows(response.data)
