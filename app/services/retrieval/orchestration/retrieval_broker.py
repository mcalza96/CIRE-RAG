import structlog
import time
import math
from typing import List, Dict, Any, Optional
from app.infrastructure.settings import settings
from app.domain.schemas.query_plan import PlannedSubQuery, QueryPlan
from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.cohere_reranker import CohereReranker
from app.services.knowledge.gravity_reranker import GravityReranker
from app.services.knowledge.jina_reranker import JinaReranker
from app.services.ingestion.metadata_enricher import enrich_metadata
from app.infrastructure.observability.forensic import ForensicRecorder
from app.infrastructure.observability.scope_metrics import scope_metrics_store
from app.domain.schemas.knowledge_schemas import RAGSearchResult, RetrievalIntent, AgentRole, TaskType
from app.domain.interfaces.reranking_provider import IAuthorityReranker, ISemanticReranker
from app.domain.interfaces.retrieval_interface import IRetrievalRepository
from app.services.knowledge.retrieval_strategies import (
    DirectRetrievalStrategy,
    IterativeRetrievalStrategy,
)
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine
from app.domain.retrieval.scope_utils import (
    apply_scope_penalty,
    clause_near_standard,
    count_scope_penalized,
    extract_requested_standards,
    extract_row_scope,
    requested_scopes_from_context,
    scope_key,
)

logger = structlog.get_logger(__name__)


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        f = float(value)
    except Exception:
        return float(default)
    return f if math.isfinite(f) else float(default)


class RetrievalBroker:
    """
    Central orchestrator for knowledge retrieval.
    Decouples DSPy tools from business logic and infrastructure.
    """

    def __init__(
        self,
        repository: IRetrievalRepository,
        *,
        authority_reranker: IAuthorityReranker | None = None,
        semantic_reranker: ISemanticReranker | None = None,
        atomic_engine: AtomicRetrievalEngine | None = None,
    ):
        self.repository = repository
        self.reranker = authority_reranker or GravityReranker()
        self.jina_reranker = semantic_reranker or JinaReranker()
        self.cohere_reranker = CohereReranker()
        self.direct_strategy = DirectRetrievalStrategy(repository)
        self.iterative_strategy = IterativeRetrievalStrategy(repository)
        self.atomic_engine = atomic_engine or AtomicRetrievalEngine()

    async def close(self) -> None:
        try:
            await self.jina_reranker.close()
            await self.cohere_reranker.close()
        except Exception as exc:
            logger.warning("retrieval_broker_close_failed", error=str(exc))

    @staticmethod
    def _engine_mode() -> str:
        mode = str(settings.RETRIEVAL_ENGINE_MODE or "hybrid").strip().lower()
        if mode == "unified":
            return "hybrid"
        if mode not in {"atomic", "hybrid"}:
            return "hybrid"
        return mode

    @staticmethod
    def _rerank_mode() -> str:
        mode = str(settings.RERANK_MODE or "hybrid").strip().lower()
        if mode not in {"local", "jina", "cohere", "hybrid"}:
            return "hybrid"
        return mode

    @staticmethod
    def _extract_requested_standards(query: str) -> tuple[str, ...]:
        return extract_requested_standards(query)

    @staticmethod
    def _normalize_standard_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = dict(filters)
        standards_raw = normalized.get("source_standards")
        standards: list[str] = []
        if isinstance(standards_raw, list):
            standards = [
                str(item).strip()
                for item in standards_raw
                if isinstance(item, str) and str(item).strip()
            ]
            standards = list(dict.fromkeys(standards))
        single = str(normalized.get("source_standard") or "").strip()

        if single and single not in standards:
            standards.insert(0, single)

        if len(standards) > 1:
            normalized["source_standards"] = standards
            normalized.pop("source_standard", None)
        elif len(standards) == 1:
            normalized["source_standard"] = standards[0]
            normalized.pop("source_standards", None)
        else:
            normalized.pop("source_standard", None)
            normalized.pop("source_standards", None)

        return normalized

    @staticmethod
    def _clause_near_standard(query: str, standard: str) -> str | None:
        return clause_near_standard(query, standard)

    @staticmethod
    def _extract_row_scope(item: Dict[str, Any]) -> str:
        return extract_row_scope(item)

    @staticmethod
    def _scope_key(value: str) -> str:
        return scope_key(value)

    @staticmethod
    def _requested_scopes_from_context(scope_context: Dict[str, Any]) -> tuple[str, ...]:
        return requested_scopes_from_context(scope_context)

    def _apply_scope_penalty(
        self, results: List[Dict[str, Any]], requested_scopes: tuple[str, ...]
    ) -> List[Dict[str, Any]]:
        penalty_factor = float(getattr(settings, "RETRIEVAL_SCOPE_PENALTY_FACTOR", 0.25) or 0.25)
        penalty_factor = max(0.0, min(0.95, penalty_factor))
        return apply_scope_penalty(results, requested_scopes, penalty_factor=penalty_factor)

    @staticmethod
    def _count_scope_penalized(results: List[Dict[str, Any]]) -> int:
        return count_scope_penalized(results)

    async def retrieve(
        self,
        query: str,
        scope_context: Dict[str, Any],
        k: int = 20,
        fetch_k: int = 120,
        enable_reranking: bool = True,
        iterative: bool = False,
        return_trace: bool = False,
        **strategy_kwargs,
    ) -> List[Dict[str, Any]] | Dict[str, Any]:
        """
        Main entry point for retrieval orchestration.
        """
        if not query:
            return {"items": [], "trace": {"timings_ms": {"total": 0.0}}} if return_trace else []

        # 1. Scope & Filter Resolution
        filter_conditions = self._resolve_filters(query, scope_context)
        trace_payload: Dict[str, Any] = {
            "filters_applied": dict(filter_conditions),
            "engine_mode": self._engine_mode(),
            "planner_used": False,
            "planner_multihop": False,
            "fallback_used": False,
            "score_space": "similarity",
            "timings_ms": {},
        }
        total_started = time.perf_counter()

        try:
            retrieval_started = time.perf_counter()
            # 2. Strategy Selection & Execution
            if iterative:
                engine = JinaEmbeddingService.get_instance()
                vectors = await engine.embed_texts([query], task="retrieval.query")
                if not vectors or not vectors[0]:
                    return (
                        {"items": [], "trace": {"timings_ms": {"total": 0.0}}}
                        if return_trace
                        else []
                    )
                vector = vectors[0]

                strategy = self.iterative_strategy
                logger.info(
                    "retrieval_strategy_execution",
                    query=query,
                    strategy=strategy.__class__.__name__,
                    filters=filter_conditions,
                )
                trace_payload["engine_mode"] = "iterative"

                raw_results = await strategy.execute(
                    query_vector=vector,
                    query_text=query,
                    filter_conditions=filter_conditions,
                    k=k,
                    fetch_k=fetch_k,
                    **strategy_kwargs,
                )
            else:
                mode = self._engine_mode()
                skip_planner_flag = bool(scope_context.get("_skip_planner"))
                external_plan = self._coerce_query_plan(scope_context.get("retrieval_plan"))
                planner_used = bool(
                    mode in {"atomic", "hybrid"}
                    and not skip_planner_flag
                    and external_plan is not None
                    and bool(external_plan.sub_queries)
                )
                if skip_planner_flag:
                    trace_payload["planner_skipped_reason"] = "multi_query_subquery"
                plan = external_plan or QueryPlan(
                    is_multihop=False,
                    execution_mode="parallel",
                    sub_queries=[],
                )
                trace_payload["engine_mode"] = mode
                trace_payload["planner_used"] = planner_used
                trace_payload["planner_multihop"] = bool(plan.is_multihop)
                trace_payload["planner_subquery_count"] = len(plan.sub_queries)
                trace_payload["planner_source"] = "request" if planner_used else "none"
                planner_skipped_reason = trace_payload.get("planner_skipped_reason")
                if plan.fallback_reason:
                    trace_payload["planner_fallback_reason"] = str(plan.fallback_reason)

                logger.info(
                    "retrieval_strategy_execution",
                    query=query,
                    strategy="AtomicRetrievalEngine",
                    filters=filter_conditions,
                    retrieval_engine_mode=mode,
                    planner_used=planner_used,
                    planner_multihop=plan.is_multihop,
                    planner_subquery_count=len(plan.sub_queries),
                    planner_fallback_reason=plan.fallback_reason,
                    planner_skipped_reason=planner_skipped_reason,
                )

                raw_results = []
                atomic_trace: Dict[str, Any] = {}
                try:
                    if plan.is_multihop:
                        raw_results = await self.atomic_engine.retrieve_context_from_plan(
                            query=query,
                            plan=plan,
                            scope_context=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                            graph_filter_relation_types=strategy_kwargs.get(
                                "graph_filter_relation_types"
                            ),
                            graph_filter_node_types=strategy_kwargs.get("graph_filter_node_types"),
                            graph_max_hops=strategy_kwargs.get("graph_max_hops"),
                        )
                    else:
                        raw_results = await self.atomic_engine.retrieve_context(
                            query=query,
                            scope_context=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                            graph_filter_relation_types=strategy_kwargs.get(
                                "graph_filter_relation_types"
                            ),
                            graph_filter_node_types=strategy_kwargs.get("graph_filter_node_types"),
                            graph_max_hops=strategy_kwargs.get("graph_max_hops"),
                        )
                    last_trace = getattr(self.atomic_engine, "last_trace", {})
                    if isinstance(last_trace, dict):
                        atomic_trace = dict(last_trace)
                except Exception as atomic_exc:
                    logger.warning("atomic_engine_failed", error=str(atomic_exc), mode=mode)
                    if mode == "atomic":
                        raise atomic_exc

                if atomic_trace:
                    contract_status = str(atomic_trace.get("rpc_contract_status") or "").strip()
                    if contract_status:
                        trace_payload["rpc_contract_status"] = contract_status
                    warnings_raw = atomic_trace.get("warnings")
                    warnings = (
                        [str(item) for item in warnings_raw if str(item).strip()]
                        if isinstance(warnings_raw, list)
                        else []
                    )
                    if warnings:
                        prior_raw = trace_payload.get("warnings")
                        prior: list[str] = (
                            [str(item) for item in prior_raw if str(item).strip()]
                            if isinstance(prior_raw, list)
                            else []
                        )
                        trace_payload["warnings"] = list(dict.fromkeys([*prior, *warnings]))
                    warning_codes_raw = atomic_trace.get("warning_codes")
                    warning_codes = (
                        [
                            str(item).strip().upper()
                            for item in warning_codes_raw
                            if str(item).strip()
                        ]
                        if isinstance(warning_codes_raw, list)
                        else []
                    )
                    if warning_codes:
                        prior_codes_raw = trace_payload.get("warning_codes")
                        prior_codes: list[str] = (
                            [
                                str(item).strip().upper()
                                for item in prior_codes_raw
                                if str(item).strip()
                            ]
                            if isinstance(prior_codes_raw, list)
                            else []
                        )
                        merged_codes = [
                            str(item).strip().upper()
                            for item in [*prior_codes, *warning_codes]
                            if str(item).strip()
                        ]
                        trace_payload["warning_codes"] = list(dict.fromkeys(merged_codes))
                    compat_mode = str(
                        atomic_trace.get("rpc_compat_mode")
                        or atomic_trace.get("hybrid_rpc_compat_mode")
                        or ""
                    ).strip()
                    if compat_mode:
                        trace_payload["rpc_compat_mode"] = compat_mode
                        trace_payload["hybrid_rpc_compat_mode"] = compat_mode
                    if "hybrid_rpc_used" in atomic_trace:
                        trace_payload["hybrid_rpc_used"] = bool(atomic_trace.get("hybrid_rpc_used"))

                if not raw_results and mode == "hybrid":
                    engine = JinaEmbeddingService.get_instance()
                    vectors = await engine.embed_texts([query], task="retrieval.query")
                    if vectors and vectors[0]:
                        logger.info(
                            "retrieval_strategy_fallback",
                            query=query,
                            strategy="DirectRetrievalStrategy",
                            reason="atomic_empty_or_failed",
                        )
                        trace_payload["fallback_used"] = True
                        raw_results = await self.direct_strategy.execute(
                            query_vector=vectors[0],
                            query_text=query,
                            filter_conditions=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                            **strategy_kwargs,
                        )

                # Literal clause fallback: if strict clause_id produced no rows, retry with broader
                # scope-only filters to recover subsection evidence (e.g., query 9.3 vs chunks 9.3.1).
                metadata_raw = filter_conditions.get("metadata")
                metadata_filter = metadata_raw if isinstance(metadata_raw, dict) else {}
                clause_id = str(metadata_filter.get("clause_id") or "").strip()
                if not raw_results and clause_id:
                    fallback_filters = dict(filter_conditions)
                    fallback_filters.pop("metadata", None)
                    nested_filters_raw = fallback_filters.get("filters")
                    if isinstance(nested_filters_raw, dict):
                        nested = {
                            str(k2): v2
                            for k2, v2 in nested_filters_raw.items()
                            if str(k2) not in {"clause_id", "clause_refs"}
                        }
                        if nested:
                            fallback_filters["filters"] = nested
                        else:
                            fallback_filters.pop("filters", None)
                    logger.info(
                        "retrieval_strategy_literal_clause_fallback",
                        clause_id=clause_id,
                        strategy="AtomicRetrievalEngine",
                    )
                    trace_payload["literal_clause_fallback"] = {
                        "applied": True,
                        "clause_id": clause_id,
                    }
                    raw_results = await self.atomic_engine.retrieve_context(
                        query=query,
                        scope_context=fallback_filters,
                        k=k,
                        fetch_k=fetch_k,
                        graph_filter_relation_types=strategy_kwargs.get(
                            "graph_filter_relation_types"
                        ),
                        graph_filter_node_types=strategy_kwargs.get("graph_filter_node_types"),
                        graph_max_hops=strategy_kwargs.get("graph_max_hops"),
                    )

            trace_payload["timings_ms"]["retrieval"] = round(
                (time.perf_counter() - retrieval_started) * 1000, 2
            )
            if not raw_results:
                ForensicRecorder.record_retrieval(
                    query, [], {"scope": scope_context.get("type"), "filters": filter_conditions}
                )
                trace_payload["timings_ms"]["total"] = round(
                    (time.perf_counter() - total_started) * 1000, 2
                )
                return {"items": [], "trace": trace_payload} if return_trace else []

            ForensicRecorder.record_retrieval(
                query,
                raw_results,
                {"scope": scope_context.get("type"), "filters": filter_conditions},
            )

            # 4. Reranking Phase
            rerank_started = time.perf_counter()
            if enable_reranking and raw_results:
                ranked = await self._apply_reranking(
                    query,
                    raw_results,
                    filter_conditions,
                    k,
                    trace_payload=trace_payload,
                )
            else:
                ranked = raw_results[:k]

            trace_payload["timings_ms"]["rerank"] = round(
                (time.perf_counter() - rerank_started) * 1000, 2
            )
            trace_payload["timings_ms"]["total"] = round(
                (time.perf_counter() - total_started) * 1000, 2
            )
            if return_trace:
                return {"items": ranked, "trace": trace_payload}
            return ranked

        except Exception as e:
            logger.error("broker_retrieval_failed", error=str(e), query=query)
            raise e

    @staticmethod
    def _coerce_query_plan(raw_plan: Any) -> QueryPlan | None:
        if isinstance(raw_plan, QueryPlan):
            return raw_plan
        if not isinstance(raw_plan, dict):
            return None

        raw_items = raw_plan.get("sub_queries")
        if not isinstance(raw_items, list):
            return None

        subqueries: list[PlannedSubQuery] = []
        for idx, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            raw_id = item.get("id")
            if isinstance(raw_id, int):
                sq_id = raw_id
            elif isinstance(raw_id, str) and raw_id.strip().isdigit():
                sq_id = int(raw_id.strip())
            else:
                sq_id = idx
            dep = item.get("dependency_id")
            dep_id = dep if isinstance(dep, int) else None
            rels = item.get("target_relations")
            nodes = item.get("target_node_types")
            target_relations = (
                [str(x).strip() for x in rels if str(x).strip()] if isinstance(rels, list) else None
            )
            target_node_types = (
                [str(x).strip() for x in nodes if str(x).strip()]
                if isinstance(nodes, list)
                else None
            )
            subqueries.append(
                PlannedSubQuery(
                    id=sq_id,
                    query=query,
                    dependency_id=dep_id,
                    target_relations=target_relations or None,
                    target_node_types=target_node_types or None,
                    is_deep=bool(item.get("is_deep", False)),
                )
            )

        if not subqueries:
            return None

        mode = str(raw_plan.get("execution_mode") or "parallel").strip().lower()
        execution_mode = "sequential" if mode == "sequential" else "parallel"
        return QueryPlan(
            is_multihop=bool(raw_plan.get("is_multihop", len(subqueries) > 1)),
            execution_mode=execution_mode,
            sub_queries=subqueries,
            fallback_reason=(str(raw_plan.get("fallback_reason") or "").strip() or None),
        )

    async def retrieve_summaries(
        self,
        query: str,
        tenant_id: str,
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves RAPTOR summaries via repository."""
        if not query or not tenant_id:
            return []

        try:
            engine = JinaEmbeddingService.get_instance()
            vectors = await engine.embed_texts([query], task="retrieval.query")
            if not vectors or not vectors[0]:
                return []

            data = await self.repository.match_summaries(
                vectors[0], tenant_id, k, collection_id=collection_id
            )
            # Ensure every row is ownership-stamped for LeakCanary (debug retrieval endpoints).
            for row in data or []:
                if not isinstance(row, dict):
                    continue
                meta_raw = row.get("metadata")
                metadata = meta_raw if isinstance(meta_raw, dict) else {}
                if meta_raw is None or not isinstance(meta_raw, dict):
                    row["metadata"] = metadata
                row.setdefault("institution_id", tenant_id)
                row.setdefault("tenant_id", tenant_id)
                metadata.setdefault("institution_id", tenant_id)
                metadata.setdefault("tenant_id", tenant_id)
                metadata.setdefault("is_raptor_summary", True)

            ForensicRecorder.record_retrieval(
                query,
                data,
                {
                    "scope": "raptor_summaries",
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                },
            )
            return data
        except Exception as e:
            logger.error("raptor_retrieval_failed", error=str(e))
            return []

    async def retrieve_graph_nodes(
        self,
        query: str,
        tenant_id: str,
        graph_options: dict[str, Any],
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieves purely topological graph context directly via AtomicEngine."""
        if not query or not tenant_id:
            return []
            
        try:
            engine = JinaEmbeddingService.get_instance()
            vectors = await engine.embed_texts([query], task="retrieval.query")
            if not vectors or not vectors[0]:
                return []
                
            scope_context = {"tenant_id": tenant_id, "collection_id": collection_id}
            
            # Use atomic engine to resolve the graph hops directly
            data = await self.atomic_engine._graph_hop(
                query_vector=vectors[0],
                scope_context=scope_context,
                fetch_k=k * 2,
                graph_filter_relation_types=graph_options.get("relation_types"),
                graph_filter_node_types=graph_options.get("node_types"),
                graph_max_hops=graph_options.get("max_hops") or 2,
            )
            
            # Ensure every row is ownership-stamped for LeakCanary
            for row in data or []:
                if not isinstance(row, dict):
                    continue
                meta_raw = row.get("metadata")
                metadata = meta_raw if isinstance(meta_raw, dict) else {}
                if meta_raw is None or not isinstance(meta_raw, dict):
                    row["metadata"] = metadata
                row.setdefault("institution_id", tenant_id)
                row.setdefault("tenant_id", tenant_id)
                metadata.setdefault("institution_id", tenant_id)
                metadata.setdefault("tenant_id", tenant_id)
                
            ForensicRecorder.record_retrieval(
                query,
                data,
                {
                    "scope": "graph_nodes",
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                },
            )
            return sorted(data, key=lambda x: float(x.get("score") or 0.0), reverse=True)[:k]
        except Exception as e:
            logger.error("graph_retrieval_failed", error=str(e))
            return []

    def _resolve_filters(self, query: str, scope_context: Dict[str, Any]) -> Dict[str, Any]:
        scope_type = scope_context.get("type", "institutional")
        filters: Dict[str, Any] = {}

        if scope_type == "institutional":
            tenant_id = scope_context.get("tenant_id")
            if not tenant_id:
                raise ValueError("Institutional scope requires 'tenant_id'.")
            filters["tenant_id"] = tenant_id
        elif scope_type == "global":
            filters["is_global"] = True

        # Merge extra filters
        extra = scope_context.get("filters", {})
        if isinstance(extra, dict) and extra:
            filters.update(extra)

        nested_raw = filters.get("filters")
        nested_filters: Dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
        if "clause_id" in nested_filters:
            nested_filters = dict(nested_filters)
            nested_filters.pop("clause_id", None)
            if nested_filters:
                filters["filters"] = nested_filters
            else:
                filters.pop("filters", None)

        scope_collection = scope_context.get("collection_id")
        if scope_collection and not filters.get("collection_id"):
            filters["collection_id"] = scope_collection

        context_scopes = self._requested_scopes_from_context(scope_context)
        query_scopes = self._extract_requested_standards(query)
        effective_scopes = context_scopes if context_scopes else query_scopes
        clause_hint_allowed = len(effective_scopes) <= 1

        # Query enrichment contributes metadata hints but cannot override validated scope standards.
        _, query_filters = enrich_metadata(query, {})
        if isinstance(query_filters, dict) and query_filters:
            metadata_raw = filters.get("metadata")
            metadata_filter = metadata_raw if isinstance(metadata_raw, dict) else {}
            metadata_clause = str(metadata_filter.get("clause_id") or "").strip()
            safe_query_filters: Dict[str, Any] = {}
            for key, value in query_filters.items():
                key_str = str(key).strip()
                if not key_str or key_str in {"source_standard", "scope"}:
                    continue
                if key_str == "clause_refs":
                    continue
                if key_str == "clause_id":
                    continue
                safe_query_filters[key_str] = value
            if safe_query_filters:
                nested_existing_raw = filters.get("filters")
                nested_existing: Dict[str, Any] = (
                    nested_existing_raw if isinstance(nested_existing_raw, dict) else {}
                )
                nested_merged = dict(nested_existing)
                nested_merged.update(safe_query_filters)
                filters["filters"] = nested_merged

        if effective_scopes:
            standards = [str(scope).strip() for scope in effective_scopes if str(scope).strip()]
            standards = list(dict.fromkeys(standards))
            if standards:
                if len(standards) == 1:
                    filters["source_standard"] = standards[0]
                    filters.pop("source_standards", None)
                else:
                    filters["source_standards"] = standards
                    filters.pop("source_standard", None)
        else:
            # Normalize any standard leftovers from merged filters.
            pass

        if clause_hint_allowed:
            metadata_existing = filters.get("metadata")
            metadata = dict(metadata_existing) if isinstance(metadata_existing, dict) else {}
            if not str(metadata.get("clause_id") or "").strip():
                active_standard = str(filters.get("source_standard") or "").strip()
                clause_hint = (
                    self._clause_near_standard(query, active_standard) if active_standard else None
                )
                if clause_hint:
                    metadata["clause_id"] = clause_hint
            if metadata:
                filters["metadata"] = metadata

        return self._normalize_standard_filters(filters)

    async def _apply_reranking(
        self,
        query: str,
        results: List[Dict],
        scope_context: Dict,
        k: int,
        *,
        trace_payload: Dict[str, Any] | None = None,
    ) -> List[Dict]:
        try:
            rerank_mode = self._rerank_mode()
            semantic_ranked = results
            requested_scopes = self._requested_scopes_from_context(scope_context)
            rerank_started = time.perf_counter()
            skip_external_rerank = bool(scope_context.get("_skip_external_rerank"))

            # Determine whether to apply local GravityReranker after external rerank.
            # "local" and "hybrid" modes use GravityReranker; "jina"/"cohere" skip it.
            use_local = rerank_mode in {"local", "hybrid"}

            # Choose the active semantic reranker based on mode
            active_reranker = None
            if not skip_external_rerank:
                if rerank_mode == "cohere" and self.cohere_reranker.is_enabled():
                    active_reranker = self.cohere_reranker
                elif rerank_mode in {"jina", "hybrid"} and self.jina_reranker.is_enabled():
                    active_reranker = self.jina_reranker

            if active_reranker and results:
                max_candidates = max(1, int(settings.RERANK_MAX_CANDIDATES or 40))
                rerank_candidates = results[:max_candidates]
                docs = [str(item.get("content") or "") for item in rerank_candidates]
                rows = await active_reranker.rerank_documents(
                    query=query,
                    documents=docs,
                    top_n=max(1, min(k, len(rerank_candidates))),
                )
                if rows:
                    reordered: list[Dict[str, Any]] = []
                    provider_label = active_reranker.__class__.__name__.lower()
                    for row in rows:
                        idx = row.get("index")
                        if not isinstance(idx, int) or idx < 0 or idx >= len(rerank_candidates):
                            continue
                        source = dict(rerank_candidates[idx])
                        # Unify score naming
                        source["semantic_relevance_score"] = _safe_float(
                            row.get("relevance_score"), default=0.0
                        )
                        if "jina" in provider_label:
                            source["jina_relevance_score"] = source["semantic_relevance_score"]
                        reordered.append(source)
                    if reordered:
                        remainder = results[len(rerank_candidates) :]
                        semantic_ranked = reordered + remainder

            if requested_scopes:
                semantic_ranked = self._apply_scope_penalty(semantic_ranked, requested_scopes)
            candidate_count = len(semantic_ranked)
            scope_penalized_count = self._count_scope_penalized(semantic_ranked)
            tenant_id = str(scope_context.get("tenant_id") or "")
            scope_metrics_store.record_rerank_penalized(
                tenant_id=tenant_id,
                penalized_count=scope_penalized_count,
                candidate_count=candidate_count,
            )

            if requested_scopes and settings.SCOPE_STRICT_FILTERING:
                strict_filtered = [
                    item for item in semantic_ranked if not bool(item.get("scope_penalized"))
                ]
                if strict_filtered:
                    semantic_ranked = strict_filtered

            scope_penalized_ratio = round(
                (scope_penalized_count / candidate_count) if candidate_count else 0.0,
                4,
            )

            if isinstance(trace_payload, dict):
                trace_payload["scope_penalized_count"] = int(scope_penalized_count)
                trace_payload["scope_candidate_count"] = int(candidate_count)
                trace_payload["scope_penalized_ratio"] = float(scope_penalized_ratio)
                trace_payload["requested_scopes"] = list(requested_scopes)

            logger.info(
                "retrieval_pipeline_timing",
                stage="rerank",
                duration_ms=round((time.perf_counter() - rerank_started) * 1000, 2),
                rerank_mode=rerank_mode,
                use_local=use_local,
                external_reranker=active_reranker.__class__.__name__ if active_reranker else "none",
                candidates=len(results),
                ranked=len(semantic_ranked),
                requested_scopes=list(requested_scopes),
                scope_penalized_count=scope_penalized_count,
                scope_penalized_ratio=scope_penalized_ratio,
            )

            if not use_local:
                if isinstance(trace_payload, dict):
                    trace_payload["score_space"] = "semantic_relevance"
                return semantic_ranked[:k]

            raw_by_id = {str(item.get("id", "")): item for item in results}
            candidates = [
                RAGSearchResult(
                    id=str(item.get("id", "")),
                    content=item.get("content", ""),
                    metadata=item.get("metadata", {}),
                    similarity=_safe_float(
                        item.get("jina_relevance_score", item.get("similarity", 0.0)),
                        default=0.0,
                    ),
                    score=_safe_float(
                        item.get("jina_relevance_score", item.get("similarity", 0.0)),
                        default=0.0,
                    ),
                    source_layer="knowledge",
                    source_id=item.get("source_id"),
                )
                for item in semantic_ranked
            ]

            intent = RetrievalIntent(
                query=query,
                role=AgentRole(scope_context.get("role", "socratic_mentor").lower()),
                task=TaskType.EXPLANATION,
            )

            ranked = self.reranker.rerank(candidates, intent)
            merged: List[Dict[str, Any]] = []
            for rc in ranked[:k]:
                payload = rc.model_dump()
                payload.setdefault("score_space", "gravity")
                source = raw_by_id.get(str(rc.id), {})
                if "is_visual_anchor" in source:
                    payload["is_visual_anchor"] = bool(source.get("is_visual_anchor"))
                if "parent_chunk_id" in source:
                    payload["parent_chunk_id"] = source.get("parent_chunk_id")
                if "source_type" in source:
                    payload["source_type"] = source.get("source_type")
                merged.append(payload)
            if isinstance(trace_payload, dict):
                trace_payload["score_space"] = "gravity"
            return merged
        except Exception as e:
            logger.warning("reranking_fallback", error=str(e))
            return results[:k]
