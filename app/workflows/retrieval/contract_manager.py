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
from app.api.middleware.security import LeakCanary, SecurityViolationError
from app.infrastructure.settings import settings
from app.domain.retrieval.scoping import (
    extract_requested_standards, 
    normalize_scope_name, 
    extract_clause_refs, 
    extract_row_scope,
    scope_clause_key
)
from app.domain.retrieval.policies import (
    apply_search_hints,
    filter_rows_by_min_score,
    reduce_structural_noise_rows,
)
from app.domain.retrieval.validation import (
    validate_metadata_values,
    validate_time_range,
    validate_source_standards,
    validate_retrieval_filters,
    matches_time_range,
    metadata_keys_matched,
    _ALLOWED_FILTER_KEYS,
)
from app.domain.retrieval.fusion import (
    fuse_late_results, 
    to_retrieval_items, 
    extract_row, 
    item_identity, 
    item_clause_refs,
    rrf_merge,
    apply_retrieval_policy_to_items,
    missing_scopes,
    missing_clause_refs,
    _safe_float
)
from app.domain.retrieval.tracing import build_comprehensive_trace
from app.infrastructure.container import CognitiveContainer
from app.workflows.retrieval.executors.late_fusion import LateFusionExecutor
from app.workflows.retrieval.executors.multi_query import MultiQueryExecutor
from app.workflows.retrieval.grounded_retrieval import GroundedRetrievalWorkflow

logger = structlog.get_logger(__name__)


def _finite_or_none(value: Any) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return None


class ContractManager:
    def __init__(
        self,
        knowledge_service: Optional[GroundedRetrievalWorkflow] = None,
        retrieval_tools: Optional[Any] = None,
    ) -> None:
        self._knowledge_service = knowledge_service or GroundedRetrievalWorkflow()
        self._retrieval_tools = retrieval_tools

    @property
    def retrieval_tools(self) -> Any:
        if self._retrieval_tools is None:
            from app.infrastructure.container import CognitiveContainer
            self._retrieval_tools = CognitiveContainer().retrieval_tools
        return self._retrieval_tools


    def validate_scope(
        self, request: ValidateScopeRequest | HybridRetrievalRequest | ExplainRetrievalRequest
    ) -> ValidateScopeResponse:
        raw_filters = request.filters.model_dump(mode="python", by_alias=True, exclude_none=True) if request.filters else {}
        warnings: list[ScopeIssue] = []

        filters_norm, violations = validate_retrieval_filters(raw_filters)

        scope_res = self._knowledge_service._resolve_scope(request.query)
        query_scope = QueryScopeSummary(
            requested_standards=list(scope_res.get("requested_standards") or []),
            requires_scope_clarification=bool(scope_res.get("requires_scope_clarification")),
            suggested_scopes=list(scope_res.get("suggested_scopes") or []),
        )
        if query_scope.requires_scope_clarification:
            warnings.append(ScopeIssue(code="SCOPE_CLARIFICATION_RECOMMENDED", field="query", message="Query ambiguous"))

        return ValidateScopeResponse(
            valid=not violations,
            normalized_scope={"tenant_id": request.tenant_id, "collection_id": request.collection_id, "filters": filters_norm},
            violations=violations,
            warnings=warnings,
            query_scope=query_scope,
        )

    @staticmethod
    def _build_scope_context(
        validated: ValidateScopeResponse, *, collection_id: str | None
    ) -> dict[str, Any]:
        normalized_filters = validated.normalized_scope.get("filters", {}) if isinstance(validated.normalized_scope, dict) else {}
        scope_context: dict[str, Any] = {
            "type": "institutional",
            "tenant_id": str(validated.normalized_scope.get("tenant_id") or "").strip() if isinstance(validated.normalized_scope, dict) else "",
            "filters": {},
        }
        if collection_id:
            scope_context["filters"]["collection_id"] = collection_id
            scope_context["collection_id"] = collection_id

        for key in ["metadata", "time_range", "source_standards", "source_standard"]:
            val = normalized_filters.get(key)
            if val:
                scope_context["filters"][key] = val
                if key in ["collection_id", "source_standards", "source_standard"]:
                    scope_context[key] = val
        return scope_context

    async def run_comprehensive(
        self,
        request: ComprehensiveRetrievalRequest,
    ) -> ComprehensiveRetrievalResponse:
        started_at = time.perf_counter()
        
        # 1. Search Hints (Expansion)
        retrieval_policy = request.retrieval_policy
        hints_payload = (
            [hint.model_dump(mode="python") for hint in retrieval_policy.search_hints]
            if retrieval_policy is not None
            else []
        )
        expanded_query, hint_trace = apply_search_hints(request.query, hints_payload)
        
        # 2. Execution
        executor = LateFusionExecutor(self.retrieval_tools, self.run_hybrid)
        merged_items, pipe_data, trace_warnings = await executor.execute(request, query=expanded_query)

        # 3. Policy application (noise reduction, min_score)
        min_score = retrieval_policy.min_score if retrieval_policy else None
        noise_reduction = bool(retrieval_policy.noise_reduction) if retrieval_policy else True
        
        final_items, policy_trace = apply_retrieval_policy_to_items(
            merged_items,
            min_score=min_score,
            noise_reduction=noise_reduction,
        )

        trace = build_comprehensive_trace(
            request=request,
            merged_items=final_items,
            chunks_trace=pipe_data["chunks_trace"],
            graph_items=pipe_data["graph_items"],
            raptor_items=pipe_data["raptor_items"],
            trace_warnings=trace_warnings,
            hint_trace=hint_trace,
            policy_trace=policy_trace,
            min_score=min_score,
            noise_reduction=noise_reduction,
            started_at=started_at,
        )

        return ComprehensiveRetrievalResponse(items=final_items, trace=trace)

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
        items = to_retrieval_items(rows)
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

    async def run_multi_query(
        self, request: MultiQueryRetrievalRequest
    ) -> MultiQueryRetrievalResponse:
        executor = MultiQueryExecutor(self.run_hybrid)
        return await executor.execute(request)


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
            row = extract_row(item)
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
                            time_range_match=matches_time_range(row, time_range),
                            metadata_keys_matched=metadata_keys_matched(row, metadata_filter),
                        ),
                    ),
                )
            )

        return ExplainRetrievalResponse(
            items=explain_items,
            trace=ExplainTrace(**hybrid.trace.model_dump(), top_n=max(1, int(request.top_n))),
        )
