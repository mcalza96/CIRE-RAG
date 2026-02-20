from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, cast
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
HYBRID_RPC_SIGNATURE_MISMATCH_HNSW = "HYBRID_RPC_SIGNATURE_MISMATCH_HNSW"


class AtomicRetrievalEngine:
    """Retrieval engine that composes atomic SQL primitives in Python."""

    _global_hybrid_rpc_contract_checked: bool = False
    _global_hybrid_rpc_contract_status: str = "unknown"
    _global_runtime_disable_hybrid_rpc: bool = False
    _global_contract_probe_error: str | None = None

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
        self._hybrid_rpc_contract_checked: bool = type(self)._global_hybrid_rpc_contract_checked
        self._hybrid_rpc_contract_status: str = type(self)._global_hybrid_rpc_contract_status
        self._runtime_disable_hybrid_rpc: bool = type(self)._global_runtime_disable_hybrid_rpc
        self._contract_probe_error: str | None = type(self)._global_contract_probe_error

    async def retrieve_context(
        self,
        query: str,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[RetrievalRow]:
        if not query.strip():
            return []

        start = perf_now()
        await self._ensure_hybrid_rpc_contract()

        self.last_trace.update(
            {
                "hybrid_rpc_enabled": bool(
                    settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc
                ),
                "hybrid_rpc_used": False,
                "rpc_contract_status": self._hybrid_rpc_contract_status,
            }
        )

        query_vector = await self._embed_query(query)
        source_ids = await self._resolve_source_ids(scope_context or {})
        if not source_ids:
            return []

        tenant_id = str((scope_context or {}).get("tenant_id") or "").strip()
        allowed_source_ids = set(str(sid) for sid in source_ids if str(sid).strip())

        vector_start = perf_now()
        fused: list[RetrievalRow] = []
        hybrid_rpc_used = False

        if settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc:
            fused, hybrid_rpc_used = await self._try_hybrid_rpc(
                query, query_vector, source_ids, fetch_k
            )

        if not hybrid_rpc_used:
            fused = await self._search_vectors(
                query_vector=query_vector, source_ids=source_ids, fetch_k=fetch_k
            )

        vector_ms = elapsed_ms(vector_start)

        graph_rows = await self._graph_hop(
            query_vector=query_vector,
            scope_context=scope_context or {},
            fetch_k=fetch_k,
            graph_filter_relation_types=graph_filter_relation_types,
            graph_filter_node_types=graph_filter_node_types,
            graph_max_hops=graph_max_hops,
        )

        merged = self._dedupe_by_id(fused + graph_rows)
        self._scope.stamp_tenant_context(
            rows=merged, tenant_id=tenant_id, allowed_source_ids=allowed_source_ids
        )

        merged, structural_trace = self._scope.filter_structural_rows(merged)

        logger.info("atomic_engine_total", duration_ms=elapsed_ms(start), merged_rows=len(merged))

        self.last_trace.update(
            {
                "hybrid_rpc_used": hybrid_rpc_used,
                "structural_filter": structural_trace,
                "timings_ms": {"total": round(elapsed_ms(start), 2), "vector": round(vector_ms, 2)},
            }
        )

        return merged[:k]

    async def _try_hybrid_rpc(self, query, query_vector, source_ids, fetch_k):
        include_hnsw = self._hybrid_rpc_contract_status != "compat_without_hnsw_ef_search"
        try:
            rows = await self._search_hybrid_rpc(
                query, query_vector, source_ids, fetch_k, include_hnsw_ef_search=include_hnsw
            )
            retrieval_metrics_store.record_hybrid_rpc_hit()
            return rows, True
        except Exception as e:
            if self._is_hnsw_signature_mismatch(e):
                logger.warning("hybrid_rpc_retry_without_hnsw")
                try:
                    rows = await self._search_hybrid_rpc(
                        query, query_vector, source_ids, fetch_k, include_hnsw_ef_search=False
                    )
                    retrieval_metrics_store.record_hybrid_rpc_hit()
                    return rows, True
                except Exception:
                    pass
            retrieval_metrics_store.record_hybrid_rpc_fallback()
            return [], False

    @staticmethod
    def _is_clause_heavy_query(query_text: str) -> bool:
        return is_clause_heavy_query(query_text)

    async def retrieve_context_from_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
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

    async def _ensure_hybrid_rpc_contract(self) -> None:
        if not bool(settings.ATOMIC_USE_HYBRID_RPC):
            self._hybrid_rpc_contract_status = "disabled"
            self._runtime_disable_hybrid_rpc = False
            retrieval_metrics_store.set_rpc_contract_status("disabled")
            return

        if self._hybrid_rpc_contract_checked:
            retrieval_metrics_store.set_rpc_contract_status(self._hybrid_rpc_contract_status)
            return

        if type(self)._global_hybrid_rpc_contract_checked:
            self._hybrid_rpc_contract_checked = True
            self._hybrid_rpc_contract_status = type(self)._global_hybrid_rpc_contract_status
            self._runtime_disable_hybrid_rpc = type(self)._global_runtime_disable_hybrid_rpc
            self._contract_probe_error = type(self)._global_contract_probe_error
            retrieval_metrics_store.set_rpc_contract_status(self._hybrid_rpc_contract_status)
            return

        self._hybrid_rpc_contract_checked = True
        dims = max(8, int(settings.JINA_EMBEDDING_DIMENSIONS or 1024))
        zero_vector = [0.0] * dims
        try:
            await self._search_hybrid_rpc(
                query_text="",
                query_vector=zero_vector,
                source_ids=[],
                fetch_k=1,
                include_hnsw_ef_search=True,
            )
            self._hybrid_rpc_contract_status = "ok"
            self._runtime_disable_hybrid_rpc = False
            self._contract_probe_error = None
        except Exception as exc:
            self._contract_probe_error = str(exc)
            if self._is_hnsw_signature_mismatch(exc):
                retrieval_metrics_store.record_rpc_contract_mismatch()
                logger.warning(
                    "atomic_hybrid_rpc_contract_mismatch_retry_compat",
                    error=str(exc),
                )
                try:
                    await self._search_hybrid_rpc(
                        query_text="",
                        query_vector=zero_vector,
                        source_ids=[],
                        fetch_k=1,
                        include_hnsw_ef_search=False,
                    )
                    self._hybrid_rpc_contract_status = "compat_without_hnsw_ef_search"
                    self._runtime_disable_hybrid_rpc = False
                    self._contract_probe_error = None
                except Exception as compat_exc:
                    self._hybrid_rpc_contract_status = "mismatch"
                    self._runtime_disable_hybrid_rpc = True
                    self._contract_probe_error = str(compat_exc)
                    logger.warning(
                        "atomic_hybrid_rpc_contract_mismatch_runtime_disabled",
                        error=str(compat_exc),
                    )
            else:
                self._hybrid_rpc_contract_status = "unknown"
                self._runtime_disable_hybrid_rpc = False
                logger.warning(
                    "atomic_hybrid_rpc_contract_probe_failed",
                    error=str(exc),
                )

        retrieval_metrics_store.set_rpc_contract_status(self._hybrid_rpc_contract_status)
        type(self)._global_hybrid_rpc_contract_checked = self._hybrid_rpc_contract_checked
        type(self)._global_hybrid_rpc_contract_status = self._hybrid_rpc_contract_status
        type(self)._global_runtime_disable_hybrid_rpc = self._runtime_disable_hybrid_rpc
        type(self)._global_contract_probe_error = self._contract_probe_error

    async def preflight_hybrid_rpc_contract(self) -> dict[str, Any]:
        await self._ensure_hybrid_rpc_contract()
        return {
            "rpc_contract_status": self._hybrid_rpc_contract_status,
            "hybrid_rpc_enabled": bool(
                settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc
            ),
            "rpc_compat_mode": (
                "runtime_disabled_on_contract_mismatch"
                if self._runtime_disable_hybrid_rpc
                else (
                    "without_hnsw_ef_search"
                    if self._hybrid_rpc_contract_status == "compat_without_hnsw_ef_search"
                    else ""
                )
            ),
            "warning_codes": (
                [HYBRID_RPC_SIGNATURE_MISMATCH_HNSW]
                if self._hybrid_rpc_contract_status == "mismatch"
                else []
            ),
            "rpc_contract_probe_error": self._contract_probe_error,
        }

    @staticmethod
    def _is_hnsw_signature_mismatch(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            "pgrst202" in text and "retrieve_hybrid_optimized" in text and "hnsw_ef_search" in text
        )

    async def _search_hybrid_rpc(
        self,
        query_text: str,
        query_vector: list[float],
        source_ids: list[str],
        fetch_k: int,
        *,
        include_hnsw_ef_search: bool = True,
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
        rpc_payload: dict[str, Any] = {
            "query_embedding": query_vector,
            "query_text": effective_query_text,
            "source_ids": source_ids,
            "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
            "match_count": max(fetch_k, 1),
            "rrf_k": settings.ATOMIC_RRF_K,
            "vector_weight": vector_weight,
            "fts_weight": effective_fts_weight,
        }
        if include_hnsw_ef_search:
            rpc_payload["hnsw_ef_search"] = max(10, int(settings.ATOMIC_HNSW_EF_SEARCH or 80))
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

    async def _resolve_source_ids(self, scope_context: dict[str, Any]) -> list[str]:
        rows = await self._retrieval_repository.list_source_documents(
            tenant_id=str(scope_context.get("tenant_id") or "").strip() or None,
            is_global=bool(scope_context.get("is_global")),
            collection_id=str(scope_context.get("collection_id") or "").strip() or None,
            limit=int(settings.ATOMIC_MAX_SOURCE_IDS),
        )
        collection_name = str(scope_context.get("collection_name") or "").strip().lower()
        source_standard = str(scope_context.get("source_standard") or "").strip().lower()
        source_standards_raw = scope_context.get("source_standards")
        source_standards: set[str] = set()
        if isinstance(source_standards_raw, list):
            source_standards = {
                str(item).strip().lower()
                for item in source_standards_raw
                if isinstance(item, str) and item.strip()
            }
        if source_standard:
            source_standards.add(source_standard)

        source_ids: list[str] = []
        nested_filters_raw = scope_context.get("filters")
        nested_filters: dict[str, Any] = (
            nested_filters_raw if isinstance(nested_filters_raw, dict) else {}
        )
        metadata_filters = scope_context.get("metadata")
        if not isinstance(metadata_filters, dict):
            metadata_filters = nested_filters.get("metadata")
        if not isinstance(metadata_filters, dict):
            metadata_filters = {}
        time_range = scope_context.get("time_range")
        if not isinstance(time_range, dict):
            time_range = nested_filters.get("time_range")
        if not isinstance(time_range, dict):
            time_range = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            if collection_name:
                row_name = str((metadata or {}).get("collection_name") or "").strip().lower()
                if row_name and row_name != collection_name:
                    continue

            if source_standards:
                candidate_values = [
                    (metadata or {}).get("source_standard"),
                    (metadata or {}).get("standard"),
                    (metadata or {}).get("scope"),
                    (metadata or {}).get("norma"),
                ]
                row_scope = ""
                for value in candidate_values:
                    if isinstance(value, str) and value.strip():
                        row_scope = value.strip().lower()
                        break
                if settings.SCOPE_STRICT_FILTERING and not row_scope:
                    continue
                if row_scope and not any(target in row_scope for target in source_standards):
                    continue

            if metadata_filters and not self._scope.matches_metadata_filters(
                metadata, metadata_filters
            ):
                continue

            if time_range and not self._scope.matches_time_range(row, time_range):
                continue

            row_id = row.get("id")
            if row_id:
                source_ids.append(str(row_id))
        return source_ids

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
