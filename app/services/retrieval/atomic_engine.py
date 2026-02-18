from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

import structlog

from app.application.services.query_decomposer import QueryPlan
from app.core.observability.retrieval_metrics import retrieval_metrics_store
from app.core.observability.timing import elapsed_ms, perf_now
from app.core.settings import settings
from app.domain.schemas.retrieval_payloads import RetrievalRow
from app.infrastructure.repositories.supabase_graph_retrieval_repository import (
    SupabaseGraphRetrievalRepository,
)
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService

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
    ):
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._supabase_client = supabase_client
        self._graph_repo = SupabaseGraphRetrievalRepository(supabase_client=supabase_client)
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
        self.last_trace = {
            "hybrid_rpc_enabled": bool(settings.ATOMIC_USE_HYBRID_RPC),
            "hybrid_rpc_used": False,
            "hybrid_rpc_compat_mode": None,
            "rpc_compat_mode": None,
            "rpc_contract_status": "unknown",
            "warnings": [],
            "warning_codes": [],
        }
        await self._ensure_hybrid_rpc_contract()
        self.last_trace["rpc_contract_status"] = self._hybrid_rpc_contract_status
        self.last_trace["hybrid_rpc_enabled"] = bool(
            settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc
        )
        if self._hybrid_rpc_contract_status == "mismatch":
            self._append_warning("hybrid_rpc_preflight_signature_mismatch")
            self._append_warning_code(HYBRID_RPC_SIGNATURE_MISMATCH_HNSW)
            self.last_trace["rpc_compat_mode"] = "runtime_disabled_on_contract_mismatch"
            self.last_trace["hybrid_rpc_compat_mode"] = "runtime_disabled_on_contract_mismatch"
        if self._contract_probe_error:
            self.last_trace["rpc_contract_probe_error"] = str(self._contract_probe_error)
        query_vector = await self._embed_query(query)
        source_ids = await self._resolve_source_ids(scope_context or {})
        if not source_ids:
            return []
        tenant_id = str((scope_context or {}).get("tenant_id") or "").strip()
        allowed_source_ids = set(str(sid) for sid in source_ids if str(sid).strip())

        vector_start = perf_now()
        vector_rows: list[RetrievalRow] = []
        fused: list[RetrievalRow] = []
        vector_ms = 0.0
        fts_ms = 0.0
        hybrid_rpc_used = False

        if settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc:
            include_hnsw_ef_search = (
                self._hybrid_rpc_contract_status != "compat_without_hnsw_ef_search"
            )
            try:
                fused = await self._search_hybrid_rpc(
                    query_text=query,
                    query_vector=query_vector,
                    source_ids=source_ids,
                    fetch_k=fetch_k,
                    include_hnsw_ef_search=include_hnsw_ef_search,
                )
                hybrid_rpc_used = True
                self.last_trace["hybrid_rpc_used"] = True
                vector_ms = elapsed_ms(vector_start)
                retrieval_metrics_store.record_hybrid_rpc_hit()
                logger.info(
                    "atomic_hybrid_rpc_used",
                    rows=len(fused),
                    source_count=len(source_ids),
                    include_hnsw_ef_search=include_hnsw_ef_search,
                )
            except Exception as hybrid_exc:
                if self._is_hnsw_signature_mismatch(hybrid_exc):
                    warning = (
                        "hybrid_rpc_signature_mismatch_hnsw_ef_search;"
                        "retrying_without_hnsw_ef_search"
                    )
                    self._append_warning(warning)
                    self._append_warning_code(HYBRID_RPC_SIGNATURE_MISMATCH_HNSW)
                    logger.warning("atomic_hybrid_rpc_signature_retry", warning=warning)
                    try:
                        fused = await self._search_hybrid_rpc(
                            query_text=query,
                            query_vector=query_vector,
                            source_ids=source_ids,
                            fetch_k=fetch_k,
                            include_hnsw_ef_search=False,
                        )
                        hybrid_rpc_used = True
                        self.last_trace["hybrid_rpc_used"] = True
                        self.last_trace["hybrid_rpc_compat_mode"] = "without_hnsw_ef_search"
                        self.last_trace["rpc_compat_mode"] = "without_hnsw_ef_search"
                        vector_ms = elapsed_ms(vector_start)
                        retrieval_metrics_store.record_hybrid_rpc_hit()
                        logger.info(
                            "atomic_hybrid_rpc_used_compat",
                            rows=len(fused),
                            source_count=len(source_ids),
                            compat_mode="without_hnsw_ef_search",
                        )
                    except Exception as compat_exc:
                        retrieval_metrics_store.record_hybrid_rpc_fallback()
                        self._append_warning(f"hybrid_rpc_fallback:{compat_exc}")
                        logger.warning("atomic_hybrid_rpc_failed_fallback", error=str(compat_exc))
                else:
                    retrieval_metrics_store.record_hybrid_rpc_fallback()
                    self._append_warning(f"hybrid_rpc_fallback:{hybrid_exc}")
                    logger.warning("atomic_hybrid_rpc_failed_fallback", error=str(hybrid_exc))
        else:
            retrieval_metrics_store.record_hybrid_rpc_disabled()

        # DB-first: if hybrid RPC is disabled or fails, we fall back to vector-only.
        # We intentionally avoid Python-side fusion (RRF) to keep ranking logic inside Postgres.
        if not hybrid_rpc_used:
            vector_rows = await self._search_vectors(
                query_vector=query_vector, source_ids=source_ids, fetch_k=fetch_k
            )
            vector_ms = elapsed_ms(vector_start)
            fused = vector_rows
        graph_rows = await self._graph_hop(
            query_vector=query_vector,
            scope_context=scope_context or {},
            fetch_k=fetch_k,
            graph_filter_relation_types=graph_filter_relation_types,
            graph_filter_node_types=graph_filter_node_types,
            graph_max_hops=graph_max_hops,
        )

        merged = self._dedupe_by_id(fused + graph_rows)
        self._stamp_tenant_context(
            rows=merged, tenant_id=tenant_id, allowed_source_ids=allowed_source_ids
        )
        logger.info(
            "retrieval_pipeline_timing",
            stage="atomic_engine_total",
            duration_ms=elapsed_ms(start),
            vector_duration_ms=vector_ms,
            fts_duration_ms=fts_ms,
            source_count=len(source_ids),
            vector_rows=len(vector_rows),
            fts_rows=0,
            graph_rows=len(graph_rows),
            merged_rows=len(merged),
            hybrid_rpc_enabled=bool(
                settings.ATOMIC_USE_HYBRID_RPC and not self._runtime_disable_hybrid_rpc
            ),
            hybrid_rpc_used=bool(hybrid_rpc_used),
            query_preview=query[:50],
        )
        self.last_trace["hybrid_rpc_used"] = bool(hybrid_rpc_used)
        self.last_trace["timings_ms"] = {
            "total": round(elapsed_ms(start), 2),
            "vector": round(vector_ms, 2),
            "fts": round(fts_ms, 2),
        }
        return merged[:k]

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

    def _append_warning(self, value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        current = self.last_trace.get("warnings")
        if not isinstance(current, list):
            self.last_trace["warnings"] = [text]
            return
        if text not in current:
            current.append(text)

    def _append_warning_code(self, value: str) -> None:
        text = str(value or "").strip().upper()
        if not text:
            return
        current = self.last_trace.get("warning_codes")
        if not isinstance(current, list):
            self.last_trace["warning_codes"] = [text]
            return
        if text not in current:
            current.append(text)

    @staticmethod
    def _stamp_tenant_context(
        *, rows: list[RetrievalRow], tenant_id: str, allowed_source_ids: set[str]
    ) -> None:
        """Attach tenant ownership metadata for rows derived from tenant-filtered source_ids.

        We do NOT blindly stamp every row, because that would mask cross-tenant leaks if a bug
        ever returns foreign content. We only stamp rows that we can tie back to the tenant's
        allowed source_ids, plus graph-derived rows (already tenant-scoped at query time).
        """

        if not tenant_id:
            return

        for row in rows:
            if not isinstance(row, dict):
                continue
            meta_raw = row.get("metadata")
            metadata: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
            if meta_raw is None or not isinstance(meta_raw, dict):
                row["metadata"] = metadata

            source_layer = str(row.get("source_layer") or "").strip().lower()
            source_id = str(metadata.get("source_id") or "").strip()
            safe_to_stamp = False
            if (
                source_layer in {"vector", "fts", "hybrid"}
                and source_id
                and source_id in allowed_source_ids
            ):
                safe_to_stamp = True
            if source_layer == "graph":
                safe_to_stamp = True

            if not safe_to_stamp:
                continue

            row.setdefault("institution_id", tenant_id)
            row.setdefault("tenant_id", tenant_id)
            metadata.setdefault("institution_id", tenant_id)
            metadata.setdefault("tenant_id", tenant_id)

    async def _search_hybrid_rpc(
        self,
        query_text: str,
        query_vector: list[float],
        source_ids: list[str],
        fetch_k: int,
        *,
        include_hnsw_ef_search: bool = True,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        effective_query_text = query_text if settings.ATOMIC_ENABLE_FTS else ""
        effective_fts_weight = settings.ATOMIC_RRF_FTS_WEIGHT if settings.ATOMIC_ENABLE_FTS else 0.0
        rpc_payload: dict[str, Any] = {
            "query_embedding": query_vector,
            "query_text": effective_query_text,
            "source_ids": source_ids,
            "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
            "match_count": max(fetch_k, 1),
            "rrf_k": settings.ATOMIC_RRF_K,
            "vector_weight": settings.ATOMIC_RRF_VECTOR_WEIGHT,
            "fts_weight": effective_fts_weight,
        }
        if include_hnsw_ef_search:
            rpc_payload["hnsw_ef_search"] = max(10, int(settings.ATOMIC_HNSW_EF_SEARCH or 80))
        response = await client.rpc(
            "retrieve_hybrid_optimized",
            rpc_payload,
        ).execute()
        rows = response.data if isinstance(response.data, list) else []
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

    async def retrieve_context_from_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[dict[str, Any]]:
        if not plan.sub_queries:
            return await self.retrieve_context(
                query=query,
                scope_context=scope_context,
                k=k,
                fetch_k=fetch_k,
                graph_filter_relation_types=graph_filter_relation_types,
                graph_filter_node_types=graph_filter_node_types,
                graph_max_hops=graph_max_hops,
            )

        if plan.execution_mode == "sequential":
            merged: list[dict[str, Any]] = []
            for sq in plan.sub_queries:
                rows = await self.retrieve_context(
                    query=sq.query,
                    scope_context=scope_context,
                    k=max(k, 12),
                    fetch_k=fetch_k,
                    graph_filter_relation_types=graph_filter_relation_types or sq.target_relations,
                    graph_filter_node_types=graph_filter_node_types or sq.target_node_types,
                    graph_max_hops=graph_max_hops
                    if graph_max_hops is not None
                    else (2 if sq.is_deep else 1),
                )
                merged.extend(rows)
            safety = await self.retrieve_context(
                query=query,
                scope_context=scope_context,
                k=max(k, 12),
                fetch_k=fetch_k,
                graph_filter_relation_types=graph_filter_relation_types,
                graph_filter_node_types=graph_filter_node_types,
                graph_max_hops=0 if graph_max_hops is None else graph_max_hops,
            )
            merged.extend(safety)
            return self._dedupe_by_id(merged)[:k]

        semaphore = asyncio.Semaphore(max(1, settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL))

        async def _bounded_retrieve(sq: Any) -> list[RetrievalRow]:
            async with semaphore:
                return await self.retrieve_context(
                    query=sq.query,
                    scope_context=scope_context,
                    k=max(k, 12),
                    fetch_k=fetch_k,
                    graph_filter_relation_types=graph_filter_relation_types or sq.target_relations,
                    graph_filter_node_types=graph_filter_node_types or sq.target_node_types,
                    graph_max_hops=graph_max_hops if graph_max_hops is not None else (2 if sq.is_deep else 1),
                )

        tasks = [_bounded_retrieve(sq) for sq in plan.sub_queries]
        tasks.append(
            self.retrieve_context(
                query=query,
                scope_context=scope_context,
                k=max(k, 12),
                fetch_k=fetch_k,
                graph_filter_relation_types=graph_filter_relation_types,
                graph_filter_node_types=graph_filter_node_types,
                graph_max_hops=0 if graph_max_hops is None else graph_max_hops,
            )
        )
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict[str, Any]] = []
        for payload in responses:
            if isinstance(payload, Exception):
                logger.warning("atomic_plan_subquery_failed", error=str(payload))
                continue
            if isinstance(payload, list):
                merged.extend(item for item in payload if isinstance(item, dict))
        return self._dedupe_by_id(merged)[:k]

    async def _search_vectors(
        self, query_vector: list[float], source_ids: list[str], fetch_k: int
    ) -> list[dict[str, Any]]:
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
        client = await self._get_client()
        query = (
            client.table("source_documents")
            .select("id,institution_id,collection_id,metadata,is_global,created_at,updated_at")
            .limit(settings.ATOMIC_MAX_SOURCE_IDS)
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

            if metadata_filters and not self._matches_metadata_filters(metadata, metadata_filters):
                continue

            if time_range and not self._matches_time_range(row, time_range):
                continue

            row_id = row.get("id")
            if row_id:
                source_ids.append(str(row_id))
        return source_ids

    @staticmethod
    def _matches_metadata_filters(metadata: dict[str, Any], expected: dict[str, Any]) -> bool:
        for key, value in expected.items():
            observed = metadata.get(str(key))
            if isinstance(value, list):
                if observed not in value:
                    return False
                continue
            if observed != value:
                return False
        return True

    @staticmethod
    def _parse_iso8601(value: str | None) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _matches_time_range(cls, row: dict[str, Any], time_range: dict[str, Any]) -> bool:
        field = str(time_range.get("field") or "").strip()
        if field not in {"created_at", "updated_at"}:
            return False
        row_value = row.get(field)
        if not isinstance(row_value, str) or not row_value.strip():
            return False
        try:
            row_dt = cls._parse_iso8601(row_value)
            dt_from = cls._parse_iso8601(time_range.get("from"))
            dt_to = cls._parse_iso8601(time_range.get("to"))
        except Exception:
            return False
        if row_dt is None:
            return False
        if dt_from and row_dt < dt_from:
            return False
        if dt_to and row_dt > dt_to:
            return False
        return True

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
