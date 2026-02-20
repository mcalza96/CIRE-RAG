from __future__ import annotations
import asyncio
import structlog
from typing import Any, List, Dict, Optional

from app.core.settings import settings
from app.domain.schemas.query_plan import QueryPlan
from app.services.retrieval.retrieval_scope_service import RetrievalScopeService

logger = structlog.get_logger(__name__)


class RetrievalPlanExecutor:
    """
    Orchestrates the execution of a QueryPlan using an AtomicRetrievalEngine.
    Handles concurrency, early exits, and result merging.
    """

    def __init__(
        self,
        atomic_engine: Any,  # Avoid circular import, type hint later if needed
        scope_service: Optional[RetrievalScopeService] = None,
    ):
        self._engine = atomic_engine
        self._scope = scope_service or RetrievalScopeService()

    async def execute_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
        graph_options: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Main entry point for plan execution.
        """
        if not plan.sub_queries:
            return await self._engine.retrieve_context(
                query=query,
                scope_context=scope_context,
                k=k,
                fetch_k=fetch_k,
                **(graph_options or {}),
            )

        max_branch_expansions = max(
            1, int(getattr(settings, "RETRIEVAL_PLAN_MAX_BRANCH_EXPANSIONS", 2) or 2)
        )
        selected_sub_queries = list(plan.sub_queries[:max_branch_expansions])

        early_exit_penalty = float(
            getattr(settings, "RETRIEVAL_PLAN_EARLY_EXIT_SCOPE_PENALTY", 0.8) or 0.8
        )
        early_exit_penalty = max(0.0, min(1.0, early_exit_penalty))

        requested_scopes = self._scope.requested_scopes(scope_context)

        # Execution State
        last_trace_update = {
            "plan_branch_policy": {
                "configured_subqueries": len(plan.sub_queries),
                "applied_subqueries": len(selected_sub_queries),
                "max_branch_expansions": max_branch_expansions,
                "early_exit_scope_penalty": early_exit_penalty,
            },
        }

        # We assume the engine has a way to update its trace or we return it
        if hasattr(self._engine, "last_trace") and isinstance(self._engine.last_trace, dict):
            self._engine.last_trace.update(last_trace_update)

        if plan.execution_mode == "sequential":
            return await self._execute_sequential(
                query=query,
                sub_queries=selected_sub_queries,
                scope_context=scope_context,
                requested_scopes=requested_scopes,
                early_exit_penalty=early_exit_penalty,
                k=k,
                fetch_k=fetch_k,
                graph_options=graph_options,
            )
        else:
            return await self._execute_parallel(
                query=query,
                sub_queries=selected_sub_queries,
                scope_context=scope_context,
                k=k,
                fetch_k=fetch_k,
                graph_options=graph_options,
            )

    async def _execute_sequential(
        self,
        query: str,
        sub_queries: list,
        scope_context: dict[str, Any] | None,
        requested_scopes: tuple[str, ...],
        early_exit_penalty: float,
        k: int,
        fetch_k: int,
        graph_options: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        early_exit_triggered = False

        for sq in sub_queries:
            sq_scope = self._scope.scope_context_for_subquery(
                scope_context=scope_context, subquery_text=sq.query
            )

            rows = await self._engine.retrieve_context(
                query=sq.query,
                scope_context=sq_scope,
                k=max(k, 12),
                fetch_k=fetch_k,
                graph_filter_relation_types=sq.target_relations,
                graph_filter_node_types=sq.target_node_types,
                graph_max_hops=(
                    graph_options.get("graph_max_hops")
                    if graph_options and "graph_max_hops" in graph_options
                    else (2 if sq.is_deep else 1)
                ),
            )
            merged.extend(rows)

            # Early Exit Logic
            penalty = self._scope.scope_penalty_ratio(rows, requested_scopes)
            if requested_scopes and rows and penalty >= early_exit_penalty:
                early_exit_triggered = True
                if hasattr(self._engine, "last_trace"):
                    self._engine.last_trace["plan_early_exit"] = {
                        "enabled": True,
                        "triggered": True,
                        "subquery_id": sq.id,
                        "scope_penalized_ratio": round(penalty, 4),
                    }
                break

        # Always run safety original query unless we are extremely confident (for now always run)
        safety = await self._engine.retrieve_context(
            query=query,
            scope_context=scope_context,
            k=max(k, 12),
            fetch_k=fetch_k,
            **(graph_options or {}),
        )
        merged.extend(safety)

        return self._dedupe(merged)[:k]

    async def _execute_parallel(
        self,
        query: str,
        sub_queries: list,
        scope_context: dict[str, Any] | None,
        k: int,
        fetch_k: int,
        graph_options: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        limit = max(1, int(getattr(settings, "RETRIEVAL_MULTI_QUERY_MAX_PARALLEL", 4)))
        semaphore = asyncio.Semaphore(limit)

        async def _bounded_retrieve(sq: Any):
            async with semaphore:
                sq_scope = self._scope.scope_context_for_subquery(
                    scope_context=scope_context, subquery_text=sq.query
                )
                return await self._engine.retrieve_context(
                    query=sq.query,
                    scope_context=sq_scope,
                    k=max(k, 12),
                    fetch_k=fetch_k,
                    graph_filter_relation_types=sq.target_relations,
                    graph_filter_node_types=sq.target_node_types,
                    graph_max_hops=(2 if sq.is_deep else 1),
                )

        tasks = [_bounded_retrieve(sq) for sq in sub_queries]
        # Main query task
        tasks.append(
            self._engine.retrieve_context(
                query=query,
                scope_context=scope_context,
                k=max(k, 12),
                fetch_k=fetch_k,
                **(graph_options or {}),
            )
        )

        responses = await asyncio.gather(*tasks, return_exceptions=True)
        merged: List[Dict[str, Any]] = []
        for res in responses:
            if isinstance(res, Exception):
                logger.warning("plan_subquery_failed", error=str(res))
                continue
            if isinstance(res, list):
                merged.extend(res)

        return self._dedupe(merged)[:k]

    @staticmethod
    def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out = []
        for item in items:
            key = str(item.get("id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out
