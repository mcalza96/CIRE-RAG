from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from app.api.v1.errors import ApiError
from app.api.v1.schemas.retrieval_advanced import (
    ExplainRetrievalRequest,
    ExplainRetrievalResponse,
    ExplainedItemDetails,
    ExplainedRetrievalItem,
    ExplainTrace,
    HybridRetrievalRequest,
    HybridRetrievalResponse,
    HybridTrace,
    MatchedFilters,
    MultiQueryRetrievalRequest,
    MultiQueryRetrievalResponse,
    MultiQueryTrace,
    QueryScopeSummary,
    RerankOptions,
    RetrievalItem,
    RetrievalPath,
    ScopeIssue,
    ScoreComponents,
    SubQueryExecution,
    SubQueryRequest,
    ValidateScopeRequest,
    ValidateScopeResponse,
)
from app.core.middleware.security import LeakCanary, SecurityViolationError
from app.core.settings import settings
from app.infrastructure.container import CognitiveContainer
from app.services.knowledge.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)

_ALLOWED_FILTER_KEYS = {"metadata", "time_range", "source_standard", "source_standards"}
_RESERVED_METADATA_KEYS = {"tenant_id", "institution_id"}
_SCALAR_TYPES = (str, int, float, bool)


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    """Return a JSON-safe finite float (no NaN/Inf)."""

    try:
        f = float(value)
    except Exception:
        return float(default)
    return f if math.isfinite(f) else float(default)


def _finite_or_none(value: Any) -> float | None:
    """Return float(value) if finite; otherwise None."""

    try:
        f = float(value)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _coerce_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    dt = datetime.fromisoformat(candidate)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_row(item: RetrievalItem) -> dict[str, Any]:
    raw = item.metadata.get("row")
    return raw if isinstance(raw, dict) else {}


class RetrievalContractService:
    def __init__(self) -> None:
        self._knowledge_service = KnowledgeService()

    @staticmethod
    def _extract_requested_standards(query: str) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for match in re.findall(r"\biso\s*[-:]?\s*(\d{4,5})\b", (query or ""), flags=re.IGNORECASE):
            value = f"ISO {match}"
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)

    @classmethod
    def _validate_metadata_values(cls, metadata: Any) -> tuple[dict[str, Any], list[ScopeIssue]]:
        violations: list[ScopeIssue] = []
        if metadata is None:
            return {}, violations
        if not isinstance(metadata, dict):
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.metadata",
                    message="metadata must be an object",
                )
            )
            return {}, violations

        normalized: dict[str, Any] = {}
        for key, value in metadata.items():
            key_str = str(key).strip()
            if not key_str:
                violations.append(
                    ScopeIssue(
                        code="INVALID_SCOPE_FILTER",
                        field="filters.metadata",
                        message="metadata keys must be non-empty",
                    )
                )
                continue
            if key_str in _RESERVED_METADATA_KEYS:
                violations.append(
                    ScopeIssue(
                        code="INVALID_SCOPE_FILTER",
                        field=f"filters.metadata.{key_str}",
                        message="tenant ownership keys are not allowed in metadata filters",
                    )
                )
                continue
            if isinstance(value, _SCALAR_TYPES):
                normalized[key_str] = value
                continue
            if isinstance(value, list) and all(isinstance(item, _SCALAR_TYPES) for item in value):
                normalized[key_str] = value
                continue
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field=f"filters.metadata.{key_str}",
                    message="metadata values must be scalar or list of scalars",
                )
            )
        return normalized, violations

    @staticmethod
    def _validate_time_range(time_range: Any) -> tuple[dict[str, Any] | None, list[ScopeIssue]]:
        violations: list[ScopeIssue] = []
        if time_range is None:
            return None, violations
        if not isinstance(time_range, dict):
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.time_range",
                    message="time_range must be an object",
                )
            )
            return None, violations

        field = str(time_range.get("field") or "").strip()
        if field not in {"created_at", "updated_at"}:
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.time_range.field",
                    message="field must be 'created_at' or 'updated_at'",
                )
            )

        try:
            dt_from = _coerce_iso8601(time_range.get("from"))
        except Exception:
            dt_from = None
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.time_range.from",
                    message="from must be valid ISO-8601 timestamp",
                )
            )
        try:
            dt_to = _coerce_iso8601(time_range.get("to"))
        except Exception:
            dt_to = None
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.time_range.to",
                    message="to must be valid ISO-8601 timestamp",
                )
            )

        if dt_from and dt_to and dt_from > dt_to:
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.time_range",
                    message="from must be <= to",
                )
            )

        if violations:
            return None, violations

        return {
            "field": field,
            "from": dt_from.isoformat() if dt_from else None,
            "to": dt_to.isoformat() if dt_to else None,
        }, []

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

        source_standard = str(raw_filters.get("source_standard") or "").strip() or None
        source_standards_raw = raw_filters.get("source_standards")
        source_standards: list[str] = []
        if source_standards_raw is not None:
            if not isinstance(source_standards_raw, list):
                violations.append(
                    ScopeIssue(
                        code="INVALID_SCOPE_FILTER",
                        field="filters.source_standards",
                        message="source_standards must be a list of strings",
                    )
                )
            else:
                for value in source_standards_raw:
                    if not isinstance(value, str) or not value.strip():
                        violations.append(
                            ScopeIssue(
                                code="INVALID_SCOPE_FILTER",
                                field="filters.source_standards",
                                message="source_standards entries must be non-empty strings",
                            )
                        )
                        continue
                    source_standards.append(value.strip())
                source_standards = list(dict.fromkeys(source_standards))

        if source_standard and source_standard not in source_standards:
            source_standards.insert(0, source_standard)
        if not source_standard and source_standards:
            source_standard = source_standards[0]

        # Enforce mutually exclusive standard selectors to avoid ambiguous filtering.
        if len(source_standards) > 1:
            source_standard = None
        elif len(source_standards) == 1:
            source_standard = source_standards[0]
            source_standards = []

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
        out: list[RetrievalItem] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            content = str(row.get("content") or "").strip()
            if not content:
                continue

            # Use the entire row dictionary as metadata.
            # This allows the orchestrator to see both top-level filterable fields
            # and nested structures (like our backfilled 'row' key).
            out.append(
                RetrievalItem(
                    source=str(row.get("source") or f"C{idx + 1}"),
                    content=content,
                    score=_safe_float(
                        row.get("similarity"), default=_safe_float(row.get("score"), default=0.0)
                    ),
                    metadata=row,
                )
            )
        return out

    @staticmethod
    def _rows_from_items(items: list[RetrievalItem]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            row = _extract_row(item)
            if row:
                rows.append(row)
        return rows

    @staticmethod
    def _normalize_scope_name(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        match = re.search(r"\bISO\s*[-:]?\s*(\d{4,5})\b", text, flags=re.IGNORECASE)
        if match:
            return f"ISO {match.group(1)}"
        digits = re.search(r"\b(\d{4,5})\b", text)
        if digits:
            return f"ISO {digits.group(1)}"
        return text

    @classmethod
    def _scope_clause_key(cls, item: SubQueryRequest) -> str:
        filters = item.filters
        scope = cls._normalize_scope_name(filters.source_standard if filters else "")
        clause_id = ""
        if filters and isinstance(filters.metadata, dict):
            clause_id = str(filters.metadata.get("clause_id") or "").strip()
        if scope and clause_id:
            return f"scope_clause::{scope}::{clause_id}"
        normalized_query = re.sub(r"\s+", " ", str(item.query or "").strip().lower())
        return f"query::{normalized_query}"

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
        if skip_planner:
            scope_context["_skip_planner"] = True
        if skip_external_rerank:
            scope_context["_skip_external_rerank"] = True
        container = CognitiveContainer.get_instance()
        rerank_enabled = bool(request.rerank.enabled) if request.rerank is not None else True
        graph_relations = request.graph.relation_types if request.graph is not None else None
        graph_node_types = request.graph.node_types if request.graph is not None else None
        graph_max_hops = request.graph.max_hops if request.graph is not None else None

        response = await container.retrieval_tools.retrieve(
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

        try:
            LeakCanary.verify_isolation(request.tenant_id, rows)
        except SecurityViolationError as exc:
            logger.critical(
                "security_isolation_breach", error=str(exc), tenant_id=request.tenant_id
            )
            raise ApiError(
                status_code=500,
                code="SECURITY_ISOLATION_BREACH",
                message="Security isolation validation failed",
                details=str(exc),
            ) from exc
        items = self._to_retrieval_items(rows)
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
                    metadata=source.metadata,
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
        try:
            LeakCanary.verify_isolation(request.tenant_id, self._rows_from_items(merged))
        except SecurityViolationError as exc:
            logger.critical(
                "security_isolation_breach", error=str(exc), tenant_id=request.tenant_id
            )
            raise ApiError(
                status_code=500,
                code="SECURITY_ISOLATION_BREACH",
                message="Security isolation validation failed",
                details=str(exc),
            ) from exc
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
            ),
        )

    @staticmethod
    def _matches_time_range(row: dict[str, Any], time_range: dict[str, Any] | None) -> bool | None:
        if not time_range:
            return None
        field = str(time_range.get("field") or "").strip()
        if field not in {"created_at", "updated_at"}:
            return False
        row_value = row.get(field)
        if not isinstance(row_value, str) or not row_value.strip():
            return False
        try:
            row_dt = _coerce_iso8601(row_value)
            range_from = _coerce_iso8601(time_range.get("from"))
            range_to = _coerce_iso8601(time_range.get("to"))
        except Exception:
            return False
        if row_dt is None:
            return False
        if range_from and row_dt < range_from:
            return False
        if range_to and row_dt > range_to:
            return False
        return True

    @staticmethod
    def _metadata_keys_matched(
        row: dict[str, Any], metadata_filter: dict[str, Any] | None
    ) -> list[str]:
        if not metadata_filter:
            return []
        raw_metadata = row.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        matched: list[str] = []
        for key, expected in metadata_filter.items():
            observed = metadata.get(key)
            if isinstance(expected, list):
                if observed in expected:
                    matched.append(key)
                continue
            if observed == expected:
                matched.append(key)
        return matched

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
