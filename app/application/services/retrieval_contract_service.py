from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.api.v1.errors import ApiError
from app.api.v1.schemas.retrieval_advanced import (
    ComprehensiveRetrievalRequest,
    ComprehensiveRetrievalResponse,
    ComprehensiveTrace,
    ExplainRetrievalRequest,
    ExplainRetrievalResponse,
    ExplainedItemDetails,
    ExplainedRetrievalItem,
    ExplainTrace,
    GraphOptions,
    HybridRetrievalRequest,
    HybridRetrievalResponse,
    HybridTrace,
    MatchedFilters,
    MergeOptions,
    MultiQueryRetrievalRequest,
    MultiQueryRetrievalResponse,
    MultiQueryTrace,
    QueryScopeSummary,
    RerankOptions,
    RetrievalItem,
    RetrievalPath,
    ScopeIssue,
    ScoreComponents,
    ScopeFilters,
    SubQueryExecution,
    SubQueryRequest,
    ValidateScopeRequest,
    ValidateScopeResponse,
)
from app.core.middleware.security import LeakCanary, SecurityViolationError
from app.core.settings import settings
from app.domain.scope_utils import extract_requested_standards, normalize_scope_name
from app.domain.scope_utils import extract_clause_refs, extract_row_scope
from app.domain.retrieval_policy import (
    apply_search_hints,
    filter_rows_by_min_score,
    reduce_structural_noise_rows,
)
from app.domain.retrieval_validation import (
    validate_metadata_values,
    validate_time_range,
    validate_source_standards,
    matches_time_range,
    metadata_keys_matched,
    _ALLOWED_FILTER_KEYS,
    _RESERVED_METADATA_KEYS
)
from app.domain.retrieval_fusion import fuse_late_results, to_retrieval_items
from app.domain.retrieval_trace import build_comprehensive_trace
from app.infrastructure.container import CognitiveContainer
from app.services.knowledge.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)

_SCALAR_TYPES = (str, int, float, bool)


from app.domain.retrieval_fusion import extract_row, item_identity, item_clause_refs, _safe_float

def _finite_or_none(value: Any) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return None

def _extract_row(item: RetrievalItem) -> dict[str, Any]:
    return extract_row(item)


class RetrievalContractService:
    def __init__(
        self,
        knowledge_service: Optional[KnowledgeService] = None,
        retrieval_tools: Optional[Any] = None,
    ) -> None:
        self._knowledge_service = knowledge_service or KnowledgeService()
        self._retrieval_tools = retrieval_tools

    @property
    def retrieval_tools(self) -> Any:
        if self._retrieval_tools is None:
            from app.infrastructure.container import CognitiveContainer
            self._retrieval_tools = CognitiveContainer().retrieval_tools
        return self._retrieval_tools

    @staticmethod
    def _validate_metadata_values(metadata: Any) -> tuple[dict[str, Any], list[ScopeIssue]]:
        return validate_metadata_values(metadata)

    @staticmethod
    def _validate_time_range(time_range: Any) -> tuple[dict[str, Any], list[ScopeIssue]]:
        return validate_time_range(time_range)

    def _validate_source_standards(self, raw_filters: dict[str, Any], violations: list[ScopeIssue]) -> tuple[str | None, list[str]]:
        source_standard, source_standards, v = validate_source_standards(raw_filters)
        violations.extend(v)
        return source_standard, source_standards

    def validate_scope(
        self, request: ValidateScopeRequest | HybridRetrievalRequest | ExplainRetrievalRequest
    ) -> ValidateScopeResponse:
        raw_filters = (
            request.filters.model_dump(mode="python", by_alias=True, exclude_none=True)
            if request.filters
            else {}
        )
        violations: list[ScopeIssue] = []
        warnings: list[ScopeIssue] = []
        unknown_keys = sorted(set(raw_filters.keys()) - _ALLOWED_FILTER_KEYS)
        for key in unknown_keys:
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field=f"filters.{key}",
                    message="filter key is not allowed",
                )
            )

        metadata_norm, metadata_violations = self._validate_metadata_values(
            raw_filters.get("metadata")
        )
        violations.extend(metadata_violations)
        time_range_norm, time_range_violations = self._validate_time_range(
            raw_filters.get("time_range")
        )
        violations.extend(time_range_violations)

        source_standard, source_standards = self._validate_source_standards(raw_filters, violations)

        scope_resolution = self._knowledge_service._resolve_scope(request.query)
        query_scope = QueryScopeSummary(
            requested_standards=list(scope_resolution.get("requested_standards") or []),
            requires_scope_clarification=bool(scope_resolution.get("requires_scope_clarification")),
            suggested_scopes=list(scope_resolution.get("suggested_scopes") or []),
        )
        if query_scope.requires_scope_clarification:
            warnings.append(
                ScopeIssue(
                    code="SCOPE_CLARIFICATION_RECOMMENDED",
                    field="query",
                    message="Query appears ambiguous; caller should disambiguate requested standard",
                )
            )

        normalized_scope = {
            "tenant_id": request.tenant_id,
            "collection_id": request.collection_id,
            "filters": {
                "metadata": metadata_norm or None,
                "time_range": time_range_norm,
                "source_standard": source_standard,
                "source_standards": source_standards or None,
            },
        }

        return ValidateScopeResponse(
            valid=len(violations) == 0,
            normalized_scope=normalized_scope,
            violations=violations,
            warnings=warnings,
            query_scope=query_scope,
        )

    @staticmethod
    def _build_scope_context(
        validated: ValidateScopeResponse, *, collection_id: str | None
    ) -> dict[str, Any]:
        normalized_filters = (
            validated.normalized_scope.get("filters")
            if isinstance(validated.normalized_scope, dict)
            else {}
        )
        filters = normalized_filters if isinstance(normalized_filters, dict) else {}
        scope_context: dict[str, Any] = {
            "type": "institutional",
            "tenant_id": str(validated.normalized_scope.get("tenant_id") or "").strip(),
            "filters": {},
        }
        if collection_id:
            scope_context["filters"]["collection_id"] = collection_id
            scope_context["collection_id"] = collection_id

        metadata = filters.get("metadata")
        if isinstance(metadata, dict) and metadata:
            scope_context["filters"]["metadata"] = metadata

        time_range = filters.get("time_range")
        if isinstance(time_range, dict) and time_range:
            scope_context["filters"]["time_range"] = time_range

        source_standards = filters.get("source_standards")
        if isinstance(source_standards, list) and source_standards:
            scope_context["filters"]["source_standards"] = list(source_standards)
            scope_context["source_standards"] = list(source_standards)

        source_standard = filters.get("source_standard")
        if isinstance(source_standard, str) and source_standard:
            scope_context["filters"]["source_standard"] = source_standard
            scope_context["source_standard"] = source_standard

        return scope_context

    @staticmethod
    def _to_retrieval_items(rows: list[dict[str, Any]]) -> list[RetrievalItem]:
        return to_retrieval_items(rows)

    @staticmethod
    def _item_identity(item: RetrievalItem) -> str:
        return item_identity(item)

    @staticmethod
    def _item_clause_refs(item: RetrievalItem) -> set[str]:
        return item_clause_refs(item)

    @staticmethod
    def _scope_clause_key(item: SubQueryRequest) -> str:
        """Deterministic key for deduplicating identical subquery intents."""
        filters = item.filters
        standard = normalize_scope_name(filters.source_standard if filters else "")
        clause_id = ""
        if filters and isinstance(filters.metadata, dict):
            clause_id = str(filters.metadata.get("clause_id") or "").strip()
        if standard and clause_id:
            return f"scope_clause::{standard}::{clause_id}"
        normalized_query = re.sub(r"\s+", " ", str(item.query or "").strip().lower())
        return f"query::{normalized_query}"

    @classmethod
    def _missing_scopes(
        cls,
        *,
        items: list[RetrievalItem],
        requested_standards: list[str],
        require_all_scopes: bool,
    ) -> list[str]:
        from app.domain.retrieval_fusion import missing_scopes
        return missing_scopes(items=items, requested_standards=requested_standards, require_all_scopes=require_all_scopes)

    @classmethod
    def _missing_clause_refs(
        cls,
        *,
        items: list[RetrievalItem],
        query_clause_refs: list[str],
        min_clause_refs_required: int,
    ) -> list[str]:
        from app.domain.retrieval_fusion import missing_clause_refs
        return missing_clause_refs(items=items, query_clause_refs=query_clause_refs, min_clause_refs_required=min_clause_refs_required)

    @staticmethod
    def _apply_retrieval_policy_to_items(
        items: list[RetrievalItem],
        *,
        min_score: float | None,
        noise_reduction: bool,
    ) -> tuple[list[RetrievalItem], dict[str, Any]]:
        from app.domain.retrieval_fusion import apply_retrieval_policy_to_items
        return apply_retrieval_policy_to_items(items, min_score=min_score, noise_reduction=noise_reduction)

    @classmethod
    def _fuse_late_results(
        cls,
        *,
        chunks: list[RetrievalItem],
        graph: list[RetrievalItem],
        raptor: list[RetrievalItem],
        k: int,
    ) -> list[RetrievalItem]:
        return fuse_late_results(chunks=chunks, graph=graph, raptor=raptor, k=k)

    async def _pipeline_chunks(
        self, 
        request: HybridRetrievalRequest, 
        trace_warnings: list[str]
    ) -> list[RetrievalItem]:
        try:
            res = await self.run_hybrid(request)
            items = res.items
            for item in items:
                item.metadata["fusion_source"] = "chunks"
            return items, res.trace.model_dump()
        except Exception as e:
            trace_warnings.append(f"chunks_pipeline_failed:{str(e)[:160]}")
            return [], {}

    async def _pipeline_graph(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        graph_options: dict[str, Any],
        k: int,
        trace_warnings: list[str]
    ) -> list[RetrievalItem]:
        try:
            raw_nodes = await self.retrieval_tools.retrieve_graph_nodes(
                query=query,
                tenant_id=tenant_id,
                graph_options=graph_options,
                k=k,
                collection_id=collection_id,
            )
            items = self._to_retrieval_items(raw_nodes)
            for item in items:
                item.metadata["fusion_source"] = "graph"
            return items
        except Exception as e:
            trace_warnings.append(f"graph_pipeline_failed:{str(e)[:160]}")
            return []

    async def _pipeline_raptor(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        k: int,
        trace_warnings: list[str]
    ) -> list[RetrievalItem]:
        try:
            raw_summaries = await self.retrieval_tools.retrieve_summaries(
                query=query,
                tenant_id=tenant_id,
                k=k,
                collection_id=collection_id,
            )
            items = self._to_retrieval_items(raw_summaries)
            for item in items:
                item.metadata["fusion_source"] = "raptor"
            return items
        except Exception as e:
            trace_warnings.append(f"raptor_pipeline_failed:{str(e)[:160]}")
            return []

    async def run_comprehensive(
        self,
        request: ComprehensiveRetrievalRequest,
    ) -> ComprehensiveRetrievalResponse:
        started = time.perf_counter()
        retrieval_policy = request.retrieval_policy
        hints_payload = (
            [hint.model_dump(mode="python") for hint in retrieval_policy.search_hints]
            if retrieval_policy is not None
            else []
        )
        expanded_query, hint_trace = apply_search_hints(request.query, hints_payload)
        
        # 1. Preparar Requests
        chunks_req = HybridRetrievalRequest(
            query=expanded_query,
            tenant_id=request.tenant_id,
            collection_id=request.collection_id,
            k=request.k,
            fetch_k=request.fetch_k,
            filters=request.filters,
            rerank=RerankOptions(enabled=True),
            graph=None,
        )
        
        graph_payload = (
            request.graph.model_dump(mode="python", exclude_none=True)
            if request.graph is not None
            else {}
        )
        graph_hops_cap = max(1, min(4, int(getattr(settings, "RETRIEVAL_COVERAGE_GRAPH_EXPANSION_MAX_HOPS", 2) or 2)))
        graph_payload["max_hops"] = max(1, min(4, max(graph_hops_cap, int(graph_payload.get("max_hops") or 0))))

        trace_warnings = []
        
        # 2. Ejecutar Pipelines Concurrentemente (Late Fusion)
        results = await asyncio.gather(
            self._pipeline_chunks(chunks_req, trace_warnings),
            self._pipeline_graph(
                expanded_query, request.tenant_id, request.collection_id, 
                graph_payload, request.k, trace_warnings
            ),
            self._pipeline_raptor(
                expanded_query, request.tenant_id, request.collection_id, 
                request.k, trace_warnings
            )
        )
        
        (chunks_items, chunks_trace), graph_items, raptor_items = results

        # 3. Ensamblaje determinista (Late Fusion)
        merged_items = self._fuse_late_results(
            chunks=chunks_items,
            graph=graph_items,
            raptor=raptor_items,
            k=request.k
        )
        
        min_score = retrieval_policy.min_score if retrieval_policy is not None else None
        noise_reduction = bool(retrieval_policy.noise_reduction) if retrieval_policy is not None else True
        merged_items, policy_trace = self._apply_retrieval_policy_to_items(
            merged_items,
            min_score=min_score,
            noise_reduction=noise_reduction,
        )

        # 4. Construcción de Trace
        trace = self._build_comprehensive_trace(
            request=request,
            merged_items=merged_items,
            chunks_trace=chunks_trace,
            graph_items=graph_items,
            raptor_items=raptor_items,
            trace_warnings=trace_warnings,
            hint_trace=hint_trace,
            policy_trace=policy_trace,
            min_score=min_score,
            noise_reduction=noise_reduction,
            started_at=started
        )

        return ComprehensiveRetrievalResponse(
            items=merged_items,
            trace=trace,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _build_comprehensive_trace(
        self,
        *,
        request: ComprehensiveRetrievalRequest,
        merged_items: list[RetrievalItem],
        chunks_trace: dict[str, Any],
        graph_items: list[RetrievalItem],
        raptor_items: list[RetrievalItem],
        trace_warnings: list[str],
        hint_trace: dict[str, Any],
        policy_trace: dict[str, Any],
        min_score: float | None,
        noise_reduction: bool,
        started_at: float
    ) -> ComprehensiveTrace:
        return build_comprehensive_trace(
            request=request,
            merged_items=merged_items,
            chunks_trace=chunks_trace,
            graph_items=graph_items,
            raptor_items=raptor_items,
            trace_warnings=trace_warnings,
            hint_trace=hint_trace,
            policy_trace=policy_trace,
            min_score=min_score,
            noise_reduction=noise_reduction,
            started_at=started_at,
            missing_scopes_callback=self._missing_scopes,
            missing_clause_refs_callback=self._missing_clause_refs
        )

    async def run_hybrid(
        self,
        request: HybridRetrievalRequest,
        *,
        skip_planner: bool = False,
        skip_external_rerank: bool = False,
    ) -> HybridRetrievalResponse:
        started = time.perf_counter()
        validated = self.validate_scope(request)
        if not validated.valid:
            raise ApiError(
                status_code=400,
                code="SCOPE_VALIDATION_FAILED",
                message="Scope validation failed",
                details={"violations": [item.model_dump() for item in validated.violations]},
            )

        scope_context = self._build_scope_context(validated, collection_id=request.collection_id)
        if request.retrieval_plan is not None:
            scope_context["retrieval_plan"] = request.retrieval_plan.model_dump(
                mode="python",
                exclude_none=True,
            )
        if skip_planner:
            scope_context["_skip_planner"] = True
        if skip_external_rerank:
            scope_context["_skip_external_rerank"] = True

        tools = self.retrieval_tools

        rerank_enabled = bool(request.rerank.enabled) if request.rerank is not None else True
        graph_relations = request.graph.relation_types if request.graph is not None else None
        graph_node_types = request.graph.node_types if request.graph is not None else None
        graph_max_hops = request.graph.max_hops if request.graph is not None else None

        response = await self.retrieval_tools.retrieve(
            query=request.query,
            scope_context=scope_context,
            k=max(1, int(request.k)),
            fetch_k=max(1, int(request.fetch_k)),
            enable_reranking=rerank_enabled,
            return_trace=True,
            graph_filter_relation_types=graph_relations,
            graph_filter_node_types=graph_node_types,
            graph_max_hops=graph_max_hops,
        )

        rows = response.get("items", []) if isinstance(response, dict) else response
        trace_payload = response.get("trace", {}) if isinstance(response, dict) else {}
        if not isinstance(rows, list):
            rows = []

        items = self._to_retrieval_items(rows)
        LeakCanary.verify_isolation(request.tenant_id, [extract_row(i) for i in items])
        warnings_raw = trace_payload.get("warnings") if isinstance(trace_payload, dict) else None
        warning_codes_raw = (
            trace_payload.get("warning_codes") if isinstance(trace_payload, dict) else None
        )
        trace_warnings = (
            [str(item) for item in warnings_raw if str(item).strip()]
            if isinstance(warnings_raw, list)
            else []
        )
        trace_warning_codes = (
            [str(item).strip().upper() for item in warning_codes_raw if str(item).strip()]
            if isinstance(warning_codes_raw, list)
            else []
        )
        if not trace_warning_codes and any(
            "signature_mismatch" in warning.lower() and "hnsw" in warning.lower()
            for warning in trace_warnings
        ):
            trace_warning_codes.append("HYBRID_RPC_SIGNATURE_MISMATCH_HNSW")
        validation_warnings = [str(item.message) for item in validated.warnings]
        merged_warnings = list(dict.fromkeys([*validation_warnings, *trace_warnings]))
        rpc_contract_status = str(trace_payload.get("rpc_contract_status") or "").strip()
        rpc_compat_mode = str(
            trace_payload.get("rpc_compat_mode")
            or trace_payload.get("hybrid_rpc_compat_mode")
            or ""
        ).strip()
        scope_penalized_count = int(trace_payload.get("scope_penalized_count") or 0)
        scope_candidate_count = int(trace_payload.get("scope_candidate_count") or 0)
        scope_penalized_ratio = _finite_or_none(trace_payload.get("scope_penalized_ratio"))
        score_space = str(trace_payload.get("score_space") or "").strip() or None
        return HybridRetrievalResponse(
            items=items,
            trace=HybridTrace(
                filters_applied=dict(trace_payload.get("filters_applied") or {}),
                engine_mode=str(
                    trace_payload.get("engine_mode") or settings.RETRIEVAL_ENGINE_MODE or "hybrid"
                ),
                planner_used=bool(trace_payload.get("planner_used", False)),
                planner_multihop=bool(trace_payload.get("planner_multihop", False)),
                fallback_used=bool(trace_payload.get("fallback_used", False)),
                rpc_contract_status=rpc_contract_status or None,
                rpc_compat_mode=rpc_compat_mode or None,
                timings_ms=dict(
                    trace_payload.get("timings_ms")
                    or {"total": round((time.perf_counter() - started) * 1000, 2)}
                ),
                warnings=merged_warnings,
                warning_codes=list(dict.fromkeys(trace_warning_codes)),
                scope_penalized_count=scope_penalized_count,
                scope_candidate_count=scope_candidate_count,
                scope_penalized_ratio=scope_penalized_ratio,
                score_space=score_space,
            ),
        )

    @staticmethod
    def _rrf_merge(
        grouped_items: list[tuple[str, list[RetrievalItem]]],
        *,
        rrf_k: int,
        top_k: int,
    ) -> list[RetrievalItem]:
        score_by_id: dict[str, float] = {}
        item_by_id: dict[str, RetrievalItem] = {}
        seq = 0
        for _, items in grouped_items:
            seq += 1
            for rank, item in enumerate(items, start=1):
                row = _extract_row(item)
                row_id = str(row.get("id") or f"synthetic-{seq}-{rank}")
                score_by_id[row_id] = score_by_id.get(row_id, 0.0) + (1.0 / (rrf_k + rank))
                if row_id not in item_by_id:
                    item_by_id[row_id] = item

        ranked_ids = sorted(score_by_id.keys(), key=lambda key: score_by_id[key], reverse=True)
        merged: list[RetrievalItem] = []
        for row_id in ranked_ids[: max(1, top_k)]:
            source = item_by_id[row_id]
            merged.append(
                RetrievalItem(
                    source=source.source,
                    content=source.content,
                    score=float(score_by_id[row_id]),
                    metadata={
                        **(source.metadata or {}),
                        "score_space": "rrf",
                    },
                )
            )
        return merged

    async def run_multi_query(
        self, request: MultiQueryRetrievalRequest
    ) -> MultiQueryRetrievalResponse:
        started = time.perf_counter()
        max_parallel = max(
            1,
            min(
                8,
                int(getattr(settings, "RETRIEVAL_MULTI_QUERY_MAX_PARALLEL", 4) or 4),
            ),
        )
        subquery_timeout_ms = max(
            200,
            int(getattr(settings, "RETRIEVAL_MULTI_QUERY_SUBQUERY_TIMEOUT_MS", 8000) or 8000),
        )
        semaphore = asyncio.Semaphore(max_parallel)
        drop_out_of_scope = bool(
            getattr(settings, "RETRIEVAL_MULTI_QUERY_DROP_SCOPE_PENALIZED_BRANCHES", True)
        )
        scope_drop_threshold = float(
            getattr(settings, "RETRIEVAL_MULTI_QUERY_SCOPE_PENALTY_DROP_THRESHOLD", 0.95) or 0.95
        )
        scope_drop_threshold = max(0.0, min(1.0, scope_drop_threshold))
        subquery_rerank_enabled = bool(
            getattr(settings, "RETRIEVAL_MULTI_QUERY_SUBQUERY_RERANK_ENABLED", False)
        )

        deduped_queries: list[SubQueryRequest] = []
        duplicate_subqueries: list[SubQueryExecution] = []
        seen_query_keys: set[str] = set()
        for item in request.queries:
            key = self._scope_clause_key(item)
            if key in seen_query_keys:
                duplicate_subqueries.append(
                    SubQueryExecution(
                        id=item.id,
                        status="error",
                        items_count=0,
                        latency_ms=0.0,
                        error_code="SUBQUERY_SKIPPED_DUPLICATE",
                        error_message="Duplicate subquery scope/clause fingerprint",
                    )
                )
                continue
            seen_query_keys.add(key)
            deduped_queries.append(item)

        async def _execute_subquery(item: Any) -> tuple[SubQueryExecution, list[RetrievalItem]]:
            sq_started = time.perf_counter()
            try:
                hybrid_request = HybridRetrievalRequest(
                    query=item.query,
                    tenant_id=request.tenant_id,
                    collection_id=request.collection_id,
                    k=item.k or request.merge.top_k,
                    fetch_k=item.fetch_k or max(request.merge.top_k * 4, 40),
                    filters=item.filters,
                    rerank=RerankOptions(enabled=subquery_rerank_enabled),
                    graph=None,
                )
                async with semaphore:
                    result = await asyncio.wait_for(
                        self.run_hybrid(
                            hybrid_request,
                            skip_planner=True,
                            skip_external_rerank=True,
                        ),
                        timeout=subquery_timeout_ms / 1000.0,
                    )

                scope_penalized_ratio: float | None = None
                trace_payload = result.trace.model_dump() if result and result.trace else {}
                if isinstance(trace_payload, dict):
                    ratio_raw = trace_payload.get("scope_penalized_ratio")
                    ratio = _finite_or_none(ratio_raw)
                    if ratio is not None:
                        scope_penalized_ratio = max(0.0, min(1.0, ratio))

                if (
                    drop_out_of_scope
                    and scope_penalized_ratio is not None
                    and scope_penalized_ratio >= scope_drop_threshold
                ):
                    return (
                        SubQueryExecution(
                            id=item.id,
                            status="error",
                            items_count=0,
                            latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                            error_code="SUBQUERY_OUT_OF_SCOPE",
                            error_message=(
                                "Branch dropped: all candidates were penalized by scope filtering"
                            ),
                        ),
                        [],
                    )

                return (
                    SubQueryExecution(
                        id=item.id,
                        status="ok",
                        items_count=len(result.items),
                        latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                    ),
                    result.items,
                )
            except TimeoutError:
                return (
                    SubQueryExecution(
                        id=item.id,
                        status="error",
                        items_count=0,
                        latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                        error_code="SUBQUERY_TIMEOUT",
                        error_message="Subquery timed out",
                    ),
                    [],
                )
            except ApiError as exc:
                return (
                    SubQueryExecution(
                        id=item.id,
                        status="error",
                        items_count=0,
                        latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                        error_code=exc.code,
                        error_message=exc.message,
                    ),
                    [],
                )
            except Exception as exc:
                return (
                    SubQueryExecution(
                        id=item.id,
                        status="error",
                        items_count=0,
                        latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                        error_code="SUBQUERY_FAILED",
                        error_message=str(exc),
                    ),
                    [],
                )

        if deduped_queries:
            executions = await asyncio.gather(
                *[_execute_subquery(item) for item in deduped_queries]
            )
        else:
            executions = []
        grouped_items: list[tuple[str, list[RetrievalItem]]] = []
        subqueries: list[SubQueryExecution] = list(duplicate_subqueries)
        failed_count = 0
        timed_out_count = 0
        for execution, items in executions:
            subqueries.append(execution)
            if execution.status == "error":
                failed_count += 1
            if execution.error_code == "SUBQUERY_TIMEOUT":
                timed_out_count += 1
            if items:
                grouped_items.append((execution.id, items))

        if not grouped_items:
            if failed_count < len(subqueries):
                # Fail-soft: all subqueries may succeed but return no evidence.
                # Return a valid empty payload so callers can trigger their own fallback policy.
                return MultiQueryRetrievalResponse(
                    items=[],
                    subqueries=subqueries,
                    partial=failed_count > 0,
                    trace=MultiQueryTrace(
                        merge_strategy=request.merge.strategy,
                        rrf_k=request.merge.rrf_k,
                        failed_count=failed_count,
                        timed_out_count=timed_out_count,
                        max_parallel=max_parallel,
                        timings_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
                        score_space="rrf",
                    ),
                )
            raise ApiError(
                status_code=502,
                code="MULTI_QUERY_ALL_FAILED",
                message="All subqueries failed",
                details={"subqueries": [sq.model_dump() for sq in subqueries]},
            )

        merged = self._rrf_merge(
            grouped_items=grouped_items,
            rrf_k=max(1, int(request.merge.rrf_k)),
            top_k=max(1, int(request.merge.top_k)),
        )
        LeakCanary.verify_isolation(request.tenant_id, [extract_row(i) for i in merged])
        return MultiQueryRetrievalResponse(
            items=merged,
            subqueries=subqueries,
            partial=failed_count > 0,
            trace=MultiQueryTrace(
                merge_strategy=request.merge.strategy,
                rrf_k=request.merge.rrf_k,
                failed_count=failed_count,
                timed_out_count=timed_out_count,
                max_parallel=max_parallel,
                timings_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
                score_space="rrf",
            ),
        )

    @staticmethod
    def _matches_time_range(row: dict[str, Any], time_range: dict[str, Any] | None) -> bool | None:
        return matches_time_range(row, time_range)

    @staticmethod
    def _metadata_keys_matched(
        row: dict[str, Any], metadata_filter: dict[str, Any] | None
    ) -> list[str]:
        return metadata_keys_matched(row, metadata_filter)

    async def run_explain(self, request: ExplainRetrievalRequest) -> ExplainRetrievalResponse:
        hybrid = await self.run_hybrid(
            HybridRetrievalRequest(
                query=request.query,
                tenant_id=request.tenant_id,
                collection_id=request.collection_id,
                k=request.k,
                fetch_k=request.fetch_k,
                filters=request.filters,
                rerank=request.rerank,
                graph=request.graph,
            )
        )
        items = hybrid.items[: max(1, int(request.top_n))]
        explain_items: list[ExplainedRetrievalItem] = []
        metadata_filter = request.filters.metadata if request.filters else None
        time_range = (
            request.filters.time_range.model_dump(mode="python", by_alias=True)
            if request.filters and request.filters.time_range
            else None
        )
        for item in items:
            row = _extract_row(item)
            base_similarity = float(row.get("similarity") or row.get("score") or item.score or 0.0)
            jina_score = row.get("jina_relevance_score")
            scope_penalty = row.get("scope_penalty")
            explain_items.append(
                ExplainedRetrievalItem(
                    source=item.source,
                    content=item.content,
                    score=_safe_float(item.score, default=0.0),
                    metadata=item.metadata,
                    explain=ExplainedItemDetails(
                        score_components=ScoreComponents(
                            base_similarity=_safe_float(base_similarity, default=0.0),
                            jina_relevance_score=(
                                _safe_float(jina_score, default=0.0)
                                if isinstance(jina_score, (int, float))
                                and math.isfinite(float(jina_score))
                                else None
                            ),
                            final_score=_safe_float(item.score, default=0.0),
                            scope_penalized=bool(row.get("scope_penalized", False)),
                            scope_penalty_ratio=_finite_or_none(scope_penalty),
                        ),
                        retrieval_path=RetrievalPath(
                            source_layer=str(row.get("source_layer") or ""),
                            source_type=str(row.get("source_type") or ""),
                        ),
                        matched_filters=MatchedFilters(
                            collection_id_match=(
                                None
                                if not request.collection_id
                                else str(
                                    row.get("collection_id")
                                    or (row.get("metadata") or {}).get("collection_id")
                                    or ""
                                )
                                == request.collection_id
                            ),
                            time_range_match=self._matches_time_range(row, time_range),
                            metadata_keys_matched=self._metadata_keys_matched(row, metadata_filter),
                        ),
                    ),
                )
            )

        return ExplainRetrievalResponse(
            items=explain_items,
            trace=ExplainTrace(**hybrid.trace.model_dump(), top_n=max(1, int(request.top_n))),
        )
