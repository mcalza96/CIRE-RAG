import asyncio
import time
from typing import Any, List, Tuple

import structlog
from app.api.v1.errors import ApiError
from app.domain.retrieval.fusion import _safe_float, extract_row
from app.domain.retrieval.scoping import scope_clause_key
from app.domain.schemas.knowledge_schemas import (
    HybridRetrievalRequest,
    MultiQueryRetrievalRequest,
    MultiQueryRetrievalResponse,
    MultiQueryTrace,
    RerankOptions,
    RetrievalItem,
    SubQueryExecution,
    SubQueryRequest,
)
from app.infrastructure.observability.forensic import LeakCanary
from app.infrastructure.settings import settings

logger = structlog.get_logger(__name__)

def _finite_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        fv = float(value)
        if not (float("-inf") < fv < float("inf")):
            return None
        return fv
    except (ValueError, TypeError):
        return None

class MultiQueryExecutor:
    def __init__(self, hybrid_runner: Any):
        self.hybrid_runner = hybrid_runner

    async def execute(self, request: MultiQueryRetrievalRequest) -> MultiQueryRetrievalResponse:
        started = time.perf_counter()
        
        max_parallel = max(1, min(8, int(getattr(settings, "RETRIEVAL_MULTI_QUERY_MAX_PARALLEL", 4) or 4)))
        subquery_timeout_ms = max(200, int(getattr(settings, "RETRIEVAL_MULTI_QUERY_SUBQUERY_TIMEOUT_MS", 8000) or 8000))
        semaphore = asyncio.Semaphore(max_parallel)
        
        drop_out_of_scope = bool(getattr(settings, "RETRIEVAL_MULTI_QUERY_DROP_SCOPE_PENALIZED_BRANCHES", True))
        scope_drop_threshold = max(0.0, min(1.0, float(getattr(settings, "RETRIEVAL_MULTI_QUERY_SCOPE_PENALTY_DROP_THRESHOLD", 0.95) or 0.95)))
        subquery_rerank_enabled = bool(getattr(settings, "RETRIEVAL_MULTI_QUERY_SUBQUERY_RERANK_ENABLED", False))

        deduped_queries: list[SubQueryRequest] = []
        duplicate_subqueries: list[SubQueryExecution] = []
        seen_query_keys: set[str] = set()
        
        for item in request.queries:
            key = scope_clause_key(item.query, item.filters)
            if key in seen_query_keys:
                duplicate_subqueries.append(SubQueryExecution(
                    id=item.id, status="error", items_count=0, latency_ms=0.0,
                    error_code="SUBQUERY_SKIPPED_DUPLICATE",
                    error_message="Duplicate subquery scope/clause fingerprint",
                ))
                continue
            seen_query_keys.add(key)
            deduped_queries.append(item)

        async def _run_one(item: SubQueryRequest) -> tuple[SubQueryExecution, list[RetrievalItem]]:
            sq_started = time.perf_counter()
            try:
                hybrid_req = HybridRetrievalRequest(
                    query=item.query, tenant_id=request.tenant_id, collection_id=request.collection_id,
                    k=item.k or request.merge.top_k, fetch_k=item.fetch_k or max(request.merge.top_k * 4, 40),
                    filters=item.filters, rerank=RerankOptions(enabled=subquery_rerank_enabled), graph=None,
                )
                async with semaphore:
                    result = await asyncio.wait_for(
                        self.hybrid_runner(hybrid_req, skip_planner=True, skip_external_rerank=True),
                        timeout=subquery_timeout_ms / 1000.0,
                    )

                trace_payload = result.trace.model_dump() if result and result.trace else {}
                ratio = _finite_or_none(trace_payload.get("scope_penalized_ratio"))
                
                if drop_out_of_scope and ratio is not None and ratio >= scope_drop_threshold:
                    return (SubQueryExecution(
                        id=item.id, status="error", items_count=0,
                        latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                        error_code="SUBQUERY_OUT_OF_SCOPE",
                        error_message="Branch dropped: all candidates were penalized by scope filtering",
                    ), [])

                return (SubQueryExecution(
                    id=item.id, status="ok", items_count=len(result.items),
                    latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                ), result.items)
            except asyncio.TimeoutError:
                return (SubQueryExecution(
                    id=item.id, status="error", items_count=0,
                    latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                    error_code="SUBQUERY_TIMEOUT", error_message="Subquery timed out",
                ), [])
            except ApiError as exc:
                return (SubQueryExecution(
                    id=item.id, status="error", items_count=0,
                    latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                    error_code=exc.code, error_message=exc.message,
                ), [])
            except Exception as exc:
                return (SubQueryExecution(
                    id=item.id, status="error", items_count=0,
                    latency_ms=round((time.perf_counter() - sq_started) * 1000, 2),
                    error_code="SUBQUERY_FAILED", error_message=str(exc),
                ), [])

        executions = await asyncio.gather(*[_run_one(item) for item in deduped_queries]) if deduped_queries else []
        
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
            elif items:
                grouped_items.append((execution.id, items))

        if not grouped_items:
            if failed_count < len(subqueries):
                return MultiQueryRetrievalResponse(
                    items=[], subqueries=subqueries, partial=failed_count > 0,
                    trace=MultiQueryTrace(
                        merge_strategy=request.merge.strategy, rrf_k=request.merge.rrf_k,
                        failed_count=failed_count, timed_out_count=timed_out_count,
                        max_parallel=max_parallel, timings_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
                        score_space="rrf",
                    )
                )
            raise ApiError(status_code=502, code="MULTI_QUERY_ALL_FAILED", message="All subqueries failed",
                           details={"subqueries": [sq.model_dump() for sq in subqueries]})

        # Merging logic should be handled by the caller or a utility
        from app.domain.retrieval.fusion import rrf_merge
        merged = rrf_merge(grouped_items=grouped_items, rrf_k=max(1, int(request.merge.rrf_k)), top_k=max(1, int(request.merge.top_k)))
        
        LeakCanary.verify_isolation(request.tenant_id, [extract_row(i) for i in merged])
        
        return MultiQueryRetrievalResponse(
            items=merged, subqueries=subqueries, partial=failed_count > 0,
            trace=MultiQueryTrace(
                merge_strategy=request.merge.strategy, rrf_k=request.merge.rrf_k,
                failed_count=failed_count, timed_out_count=timed_out_count,
                max_parallel=max_parallel, timings_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
                score_space="rrf",
            )
        )
