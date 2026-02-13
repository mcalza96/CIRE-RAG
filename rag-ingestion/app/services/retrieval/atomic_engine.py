from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog

from app.application.services.query_decomposer import QueryPlan
from app.core.settings import settings
from app.infrastructure.repositories.supabase_graph_retrieval_repository import SupabaseGraphRetrievalRepository
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService

logger = structlog.get_logger(__name__)


class AtomicRetrievalEngine:
    """Retrieval engine that composes atomic SQL primitives in Python."""

    def __init__(self, embedding_service: JinaEmbeddingService | None = None, supabase_client: Any | None = None):
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._supabase_client = supabase_client
        self._graph_repo = SupabaseGraphRetrievalRepository(supabase_client=supabase_client)

    async def retrieve_context(
        self,
        query: str,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []

        start = time.perf_counter()
        query_vector = await self._embed_query(query)
        source_ids = await self._resolve_source_ids(scope_context or {})
        if not source_ids:
            return []

        vector_start = time.perf_counter()
        vector_rows = await self._search_vectors(query_vector=query_vector, source_ids=source_ids, fetch_k=fetch_k)
        vector_ms = round((time.perf_counter() - vector_start) * 1000, 2)

        fts_rows: list[dict[str, Any]] = []
        fts_ms = 0.0
        if settings.ATOMIC_ENABLE_FTS:
            fts_start = time.perf_counter()
            fts_rows = await self._search_fts(query_text=query, source_ids=source_ids, fetch_k=fetch_k)
            fts_ms = round((time.perf_counter() - fts_start) * 1000, 2)

        fused = self._fuse_rrf(vector_rows=vector_rows, fts_rows=fts_rows, k=max(fetch_k, k))
        graph_rows = await self._graph_hop(query_vector=query_vector, scope_context=scope_context or {}, fetch_k=fetch_k)

        merged = self._dedupe_by_id(fused + graph_rows)
        logger.info(
            "retrieval_pipeline_timing",
            stage="atomic_engine_total",
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
            vector_duration_ms=vector_ms,
            fts_duration_ms=fts_ms,
            source_count=len(source_ids),
            vector_rows=len(vector_rows),
            fts_rows=len(fts_rows),
            graph_rows=len(graph_rows),
            merged_rows=len(merged),
            query_preview=query[:50],
        )
        return merged[:k]

    async def retrieve_context_from_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
    ) -> list[dict[str, Any]]:
        if not plan.sub_queries:
            return await self.retrieve_context(query=query, scope_context=scope_context, k=k, fetch_k=fetch_k)

        if plan.execution_mode == "sequential":
            merged: list[dict[str, Any]] = []
            for sq in plan.sub_queries:
                rows = await self.retrieve_context(query=sq.query, scope_context=scope_context, k=max(k, 12), fetch_k=fetch_k)
                merged.extend(rows)
            return self._dedupe_by_id(merged)[:k]

        tasks = [
            self.retrieve_context(query=sq.query, scope_context=scope_context, k=max(k, 12), fetch_k=fetch_k)
            for sq in plan.sub_queries
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict[str, Any]] = []
        for payload in responses:
            if isinstance(payload, Exception):
                logger.warning("atomic_plan_subquery_failed", error=str(payload))
                continue
            if isinstance(payload, list):
                merged.extend(payload)
        return self._dedupe_by_id(merged)[:k]

    async def _search_vectors(self, query_vector: list[float], source_ids: list[str], fetch_k: int) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc(
            "search_vectors_only",
            {
                "query_embedding": query_vector,
                "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            },
        ).execute()
        rows = response.data if isinstance(response.data, list) else []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("similarity") or 0.0),
                    "source_layer": "vector",
                    "source_type": "content_chunk",
                    "source_id": metadata.get("source_id"),
                }
            )
        return normalized

    async def _search_fts(self, query_text: str, source_ids: list[str], fetch_k: int) -> list[dict[str, Any]]:
        client = await self._get_client()
        response = await client.rpc(
            "search_fts_only",
            {
                "query_text": query_text,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            },
        ).execute()
        rows = response.data if isinstance(response.data, list) else []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("rank") or 0.0),
                    "score": float(row.get("rank") or 0.0),
                    "source_layer": "fts",
                    "source_type": "content_chunk",
                    "source_id": metadata.get("source_id"),
                }
            )
        return normalized

    async def _graph_hop(self, query_vector: list[float], scope_context: dict[str, Any], fetch_k: int) -> list[dict[str, Any]]:
        if not settings.ATOMIC_ENABLE_GRAPH_HOP:
            return []

        tenant_id = scope_context.get("tenant_id")
        if not tenant_id:
            return []

        try:
            tenant_uuid = UUID(str(tenant_id))
        except Exception:
            return []

        try:
            rows = await self._graph_repo.search_multi_hop_context(
                tenant_id=tenant_uuid,
                query_vector=query_vector,
                match_threshold=min(settings.ATOMIC_MATCH_THRESHOLD, 0.35),
                limit_count=max(min(fetch_k, 12), 6),
                max_hops=2,
                decay_factor=0.82,
            )
        except Exception as exc:
            logger.warning("atomic_graph_hop_failed", error=str(exc))
            return []

        out: list[dict[str, Any]] = []
        for row in rows:
            entity_id = str(row.get("entity_id") or "")
            if not entity_id:
                continue
            hop_depth = int(row.get("hop_depth") or 0)
            name = str(row.get("entity_name") or "Unknown")
            description = str(row.get("entity_description") or "").strip()
            out.append(
                {
                    "id": f"graph:{entity_id}",
                    "content": f"[{('anchor' if hop_depth == 0 else f'hop-{hop_depth}')}] {name}: {description}",
                    "metadata": {
                        "citations": [entity_id],
                        "path_ids": row.get("path_ids") or [],
                        "hop_depth": hop_depth,
                    },
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("similarity") or 0.0),
                    "source_layer": "graph",
                    "source_type": "knowledge_entity",
                    "source_id": entity_id,
                }
            )
        return out

    async def _resolve_source_ids(self, scope_context: dict[str, Any]) -> list[str]:
        client = await self._get_client()
        query = client.table("source_documents").select("id,institution_id,collection_id,metadata,is_global").limit(
            settings.ATOMIC_MAX_SOURCE_IDS
        )

        tenant_id = scope_context.get("tenant_id")
        if tenant_id:
            query = query.eq("institution_id", str(tenant_id))
        elif scope_context.get("is_global"):
            query = query.eq("is_global", True)

        collection_id = scope_context.get("collection_id")
        if collection_id:
            query = query.eq("collection_id", str(collection_id))

        response = await query.execute()
        rows = response.data if isinstance(response.data, list) else []
        collection_name = str(scope_context.get("collection_name") or "").strip().lower()
        source_ids: list[str] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if collection_name:
                row_name = str(metadata.get("collection_name") or "").strip().lower()
                if row_name and row_name != collection_name:
                    continue
            row_id = row.get("id")
            if row_id:
                source_ids.append(str(row_id))
        return source_ids

    def _fuse_rrf(self, vector_rows: list[dict[str, Any]], fts_rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
        score_by_id: dict[str, float] = {}
        doc_by_id: dict[str, dict[str, Any]] = {}

        def add(rows: list[dict[str, Any]], weight: float) -> None:
            for rank, row in enumerate(rows, start=1):
                row_id = str(row.get("id") or "")
                if not row_id:
                    continue
                score_by_id[row_id] = score_by_id.get(row_id, 0.0) + (weight / (settings.ATOMIC_RRF_K + rank))
                if row_id not in doc_by_id:
                    doc_by_id[row_id] = dict(row)

        add(vector_rows, settings.ATOMIC_RRF_VECTOR_WEIGHT)
        add(fts_rows, settings.ATOMIC_RRF_FTS_WEIGHT)

        ranked_ids = sorted(score_by_id.keys(), key=lambda rid: score_by_id[rid], reverse=True)
        fused: list[dict[str, Any]] = []
        for rid in ranked_ids[:k]:
            item = dict(doc_by_id[rid])
            item["score"] = float(score_by_id[rid])
            item["similarity"] = float(max(item.get("similarity") or 0.0, item.get("score") or 0.0))
            fused.append(item)
        return fused

    @staticmethod
    def _dedupe_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in items:
            key = str(item.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await self._embedding_service.embed_texts([query], task="retrieval.query")
        if not vectors or not vectors[0]:
            raise ValueError("Failed to generate query embedding.")
        return vectors[0]

    async def _get_client(self) -> Any:
        if self._supabase_client is not None:
            return self._supabase_client
        self._supabase_client = await get_async_supabase_client()
        return self._supabase_client
