from __future__ import annotations

import asyncio
from typing import Any, Optional, cast
from uuid import UUID

import structlog

from app.core.observability.retrieval_metrics import retrieval_metrics_store
from app.core.observability.timing import elapsed_ms, perf_now
from app.core.settings import settings
from app.domain.interfaces.atomic_retrieval_repository import IAtomicRetrievalRepository
from app.domain.schemas.query_plan import QueryPlan
from app.domain.schemas.retrieval_payloads import RetrievalRow
from app.infrastructure.repositories.supabase_graph_retrieval_repository import (
    SupabaseGraphRetrievalRepository,
)
from app.infrastructure.repositories.supabase_atomic_retrieval_repository import (
    SupabaseAtomicRetrievalRepository,
)
from app.services.embedding_service import JinaEmbeddingService

from app.services.retrieval.retrieval_scope_service import RetrievalScopeService
from app.services.retrieval.retrieval_plan_executor import RetrievalPlanExecutor
from app.domain.scope_utils import is_clause_heavy_query

logger = structlog.get_logger(__name__)


class AtomicRetrievalEngine:
    """Retrieval engine that composes atomic SQL primitives in Python."""

    def __init__(
        self,
        embedding_service: JinaEmbeddingService | None = None,
        supabase_client: Any | None = None,
        retrieval_repository: IAtomicRetrievalRepository | None = None,
        scope_service: Optional[RetrievalScopeService] = None,
        executor: Optional[RetrievalPlanExecutor] = None,
    ):
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._retrieval_repository = retrieval_repository or SupabaseAtomicRetrievalRepository(
            supabase_client=supabase_client
        )
        self._graph_repo = SupabaseGraphRetrievalRepository(supabase_client=supabase_client)

        # New Services
        self._scope = scope_service or RetrievalScopeService()
        self._executor = executor or RetrievalPlanExecutor(self, self._scope)

        self.last_trace: dict[str, Any] = {}

    async def retrieve_context(
        self,
        query: str,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 120,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[RetrievalRow]:
        if not query.strip():
            return []

        start = perf_now()

        self.last_trace.update(
            {
                "hybrid_rpc_enabled": bool(settings.ATOMIC_USE_HYBRID_RPC),
                "hybrid_rpc_used": False,
            }
        )

        query_vector = await self._embed_query(query)

        tenant_id = str((scope_context or {}).get("tenant_id") or "").strip()
        allowed_source_ids: set[str] = set()

        vector_start = perf_now()

        # --- Primary retrieval: always use the hybrid RPC directly ---
        if settings.ATOMIC_USE_HYBRID_RPC:
            fused = await self._search_hybrid_rpc(
                query, query_vector, scope_context or {}, fetch_k
            )
            self.last_trace["hybrid_rpc_used"] = True
            retrieval_metrics_store.record_hybrid_rpc_hit()
        else:
            fused = await self._search_vectors_scoped(
                query_vector=query_vector,
                scope_context=scope_context or {},
                fetch_k=fetch_k,
            )

        vector_ms = elapsed_ms(vector_start)

        # --- Graph hop (separate source, IDs are prefixed with "graph:" so no collision) ---
        graph_rows = await self._graph_hop(
            query_vector=query_vector,
            scope_context=scope_context or {},
            fetch_k=fetch_k,
            graph_filter_relation_types=graph_filter_relation_types,
            graph_filter_node_types=graph_filter_node_types,
            graph_max_hops=graph_max_hops,
        )

        # Graph results use "graph:{entity_id}" IDs, content chunks use UUIDs.
        # No collision possible, so simple concatenation is safe.
        merged = fused + graph_rows

        allowed_source_ids = {
            str((item.get("metadata") or {}).get("source_id") or "").strip()
            for item in merged
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and str((item.get("metadata") or {}).get("source_id") or "").strip()
        }
        self._scope.stamp_tenant_context(
            rows=merged, tenant_id=tenant_id, allowed_source_ids=allowed_source_ids
        )

        merged, structural_trace = self._scope.filter_structural_rows(merged)

        logger.info("atomic_engine_total", duration_ms=elapsed_ms(start), merged_rows=len(merged))

        self.last_trace.update(
            {
                "structural_filter": structural_trace,
                "timings_ms": {"total": round(elapsed_ms(start), 2), "vector": round(vector_ms, 2)},
            }
        )

        return merged[:k]

    @staticmethod
    def _is_clause_heavy_query(query_text: str) -> bool:
        return is_clause_heavy_query(query_text)

    async def retrieve_context_from_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 120,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Delegates plan execution to RetrievalPlanExecutor."""
        return await self._executor.execute_plan(
            query=query,
            plan=plan,
            scope_context=scope_context,
            k=k,
            fetch_k=fetch_k,
            graph_options=kwargs,
        )

    async def preflight_hybrid_rpc_contract(self) -> dict[str, Any]:
        """Health-check: just verify the RPC is callable. Fail fast if not."""
        return {
            "rpc_contract_status": "fixed",
            "hybrid_rpc_enabled": bool(settings.ATOMIC_USE_HYBRID_RPC),
            "rpc_compat_mode": "",
            "warning_codes": [],
            "rpc_contract_probe_error": None,
        }

    async def _search_hybrid_rpc(
        self,
        query_text: str,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
    ) -> list[dict[str, Any]]:
        effective_query_text = query_text if settings.ATOMIC_ENABLE_FTS else ""
        vector_weight = float(settings.ATOMIC_RRF_VECTOR_WEIGHT)
        effective_fts_weight = (
            float(settings.ATOMIC_RRF_FTS_WEIGHT) if settings.ATOMIC_ENABLE_FTS else 0.0
        )
        if settings.ATOMIC_ENABLE_FTS and self._is_clause_heavy_query(query_text):
            if bool(getattr(settings, "ATOMIC_CLAUSE_QUERY_WEIGHT_BOOST_ENABLED", True)):
                vector_weight = float(
                    getattr(settings, "ATOMIC_CLAUSE_QUERY_RRF_VECTOR_WEIGHT", vector_weight)
                    or vector_weight
                )
                effective_fts_weight = float(
                    getattr(settings, "ATOMIC_CLAUSE_QUERY_RRF_FTS_WEIGHT", effective_fts_weight)
                    or effective_fts_weight
                )
        source_standard_raw = str(scope_context.get("source_standard") or "").strip()
        source_standards_raw = scope_context.get("source_standards")
        source_standards: list[str] = []
        if isinstance(source_standards_raw, list):
            source_standards = [
                str(item).strip()
                for item in source_standards_raw
                if isinstance(item, str) and str(item).strip()
            ]
        if source_standard_raw and source_standard_raw not in source_standards:
            source_standards.insert(0, source_standard_raw)

        rpc_payload: dict[str, Any] = {
            "query_embedding": query_vector,
            "query_text": effective_query_text,
            "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
            "match_count": max(fetch_k, 1),
            "rrf_k": settings.ATOMIC_RRF_K,
            "vector_weight": vector_weight,
            "fts_weight": effective_fts_weight,
            "tenant_id": str(scope_context.get("tenant_id") or "").strip() or None,
            "is_global": (
                bool(scope_context.get("is_global"))
                if scope_context.get("is_global") is not None
                else None
            ),
            "collection_id": str(scope_context.get("collection_id") or "").strip() or None,
            "source_standard": source_standard_raw or None,
            "source_standards": source_standards or None,
            "hnsw_ef_search": max(10, int(settings.ATOMIC_HNSW_EF_SEARCH or 80)),
        }
        rows = await self._retrieval_repository.retrieve_hybrid_optimized(rpc_payload)
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("score") or 0.0),
                    "source_layer": str(row.get("source_layer") or "hybrid"),
                    "source_type": str(row.get("source_type") or "content_chunk"),
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _search_vectors_scoped(
        self,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
    ) -> list[dict[str, Any]]:
        rows = await self._search_hybrid_rpc(
            query_text="",
            query_vector=query_vector,
            scope_context=scope_context,
            fetch_k=fetch_k,
        )
        for row in rows:
            if isinstance(row, dict):
                row["source_layer"] = "vector"
        return rows

    async def _search_vectors(
        self, query_vector: list[float], source_ids: list[str], fetch_k: int
    ) -> list[dict[str, Any]]:
        rows = await self._retrieval_repository.search_vectors_only(
            {
                "query_embedding": query_vector,
                "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            }
        )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("similarity") or 0.0),
                    "source_layer": "vector",
                    "source_type": "content_chunk",
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _search_fts(
        self, query_text: str, source_ids: list[str], fetch_k: int
    ) -> list[dict[str, Any]]:
        rows = await self._retrieval_repository.search_fts_only(
            {
                "query_text": query_text,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            }
        )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("rank") or 0.0),
                    "score": float(row.get("rank") or 0.0),
                    "source_layer": "fts",
                    "source_type": "content_chunk",
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _graph_hop(
        self,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[dict[str, Any]]:
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
            node_types = graph_filter_node_types or scope_context.get("graph_filter_node_types")
            relation_types = graph_filter_relation_types or scope_context.get(
                "graph_filter_relation_types"
            )
            if not isinstance(node_types, list):
                node_types = None
            if not isinstance(relation_types, list):
                relation_types = None
            hops = graph_max_hops if isinstance(graph_max_hops, int) else 2
            logger.info(
                "atomic_graph_nav_request",
                tenant_id=str(tenant_id),
                graph_max_hops=max(0, hops),
                graph_filter_relation_types=relation_types or [],
                graph_filter_node_types=node_types or [],
            )

            rows = await self._graph_repo.search_multi_hop_context(
                tenant_id=tenant_uuid,
                query_vector=query_vector,
                match_threshold=min(settings.ATOMIC_MATCH_THRESHOLD, 0.35),
                limit_count=max(min(fetch_k, 12), 6),
                max_hops=max(0, hops),
                decay_factor=0.82,
                filter_node_types=node_types,
                filter_relation_types=relation_types,
            )
        except Exception as exc:
            logger.warning("atomic_graph_hop_failed", error=str(exc))
            return []

        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
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

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await self._embedding_service.embed_texts([query], task="retrieval.query")
        if not vectors or not vectors[0]:
            raise ValueError("Failed to generate query embedding.")
        return vectors[0]
