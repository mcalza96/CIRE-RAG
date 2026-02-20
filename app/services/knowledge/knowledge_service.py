import re
from typing import List, Dict, Any
from uuid import uuid4
import structlog
from app.api.v1.errors import ApiError
from app.core.middleware.security import SecurityViolationError
from app.core.retrieval_config import retrieval_settings
from app.core.settings import settings
from app.core.observability.scope_metrics import scope_metrics_store
from app.core.observability.timing import elapsed_ms, perf_now
from app.application.services.retrieval_router import RetrievalRouter
from app.domain.knowledge_schemas import RetrievalIntent, AgentRole, TaskType
from app.domain.schemas.retrieval_payloads import GroundedContext

from app.domain.interfaces.scope_resolver_policy import IScopeResolverPolicy
from app.services.knowledge.iso_scope_strategy import ISOScopeResolverPolicy
from typing import Optional

logger = structlog.get_logger(__name__)


class KnowledgeService:
    """
    Application Service for Basic Knowledge Retrieval.
    Uses retrieval router when enabled.
    """

    def __init__(
        self, 
        container: Optional[Any] = None,
        scope_policy: Optional[IScopeResolverPolicy] = None
    ) -> None:
        self._container = container
        self._scope_policy = scope_policy or ISOScopeResolverPolicy()

    async def get_grounded_context(
        self, query: str, institution_id: str, k: int = retrieval_settings.TOP_K
    ) -> GroundedContext:
        """
        Retrieves context via vector search.
        """
        retrieval_request_id = str(uuid4())
        start = perf_now()
        selected_mode = "retrieval_router" if self._use_retrieval_router() else "vector_only"
        scope_metrics_store.record_request(institution_id)
        scope_resolution = self._resolve_scope(query)
        logger.info(
            "scope_resolution",
            query_preview=query[:80],
            tenant_id=institution_id,
            requested_scopes=list(scope_resolution["requested_standards"]),
            requires_scope_clarification=scope_resolution["requires_scope_clarification"],
            scope_candidates=list(scope_resolution["suggested_scopes"]),
        )

        if scope_resolution["requires_scope_clarification"]:
            suggested = ", ".join(scope_resolution["suggested_scopes"])
            scope_metrics_store.record_clarification(institution_id)
            logger.info(
                "scope_clarification_required",
                tenant_id=institution_id,
                query_preview=query[:80],
                scope_candidates=list(scope_resolution["suggested_scopes"]),
            )
            return {
                "context_chunks": [],
                "context_map": {},
                "citations": [],
                "mode": "AMBIGUOUS_SCOPE",
                "requires_scope_clarification": True,
                "scope_candidates": scope_resolution["suggested_scopes"],
                "scope_message": (
                    "Necesito desambiguar la norma objetivo antes de responder con trazabilidad. "
                    f"Sugeridas: {suggested}."
                ),
            }

        logger.info(
            "Retrieving grounded context",
            query_preview=query[:50],
            institution_id=institution_id,
            mode=selected_mode,
            retrieval_request_id=retrieval_request_id,
        )

        if self._container is None:
            from app.infrastructure.container import CognitiveContainer
            self._container = CognitiveContainer()

        container = self._container

        if self._use_retrieval_router():
            result = await self._get_context_retrieval_router(
                query,
                institution_id,
                k,
                container,
                retrieval_request_id,
                requested_standards=scope_resolution["requested_standards"],
            )
        else:
            result = await self._get_context_vector_only(
                query,
                institution_id,
                k,
                container,
                retrieval_request_id,
                requested_standards=scope_resolution["requested_standards"],
            )

        pipeline_ms = elapsed_ms(start)
        logger.info(
            "retrieval_pipeline_timing",
            stage="pipeline_total",
            mode=selected_mode,
            retrieval_request_id=retrieval_request_id,
            tenant_id=institution_id,
            duration_ms=pipeline_ms,
            result_chunks=len(result.get("context_chunks", []) or []),
        )
        return result

    @staticmethod
    def _use_retrieval_router() -> bool:
        return settings.USE_TRICAMERAL

    async def _get_context_retrieval_router(
        self,
        query: str,
        institution_id: str,
        k: int,
        container: Any,
        retrieval_request_id: str,
        requested_standards: tuple[str, ...] = (),
    ) -> GroundedContext:
        from app.core.middleware.security import LeakCanary

        orchestrator = RetrievalRouter(
            vector_tools=container.retrieval_tools,
        )

        intent = RetrievalIntent(
            query=query,
            tenant_id=institution_id,
            role=AgentRole.SOCRATIC_MENTOR,
            task=TaskType.EXPLANATION,
            metadata={
                "retrieval_request_id": retrieval_request_id,
                "requested_scopes": list(requested_standards),
            },
        )

        output = await orchestrator.orchestrate(intent=intent, k=k)
        results = output.get("results", []) or []
        filtered_results = self._filter_results_by_scope(results, requested_standards)
        scope_mismatch = bool(requested_standards and results and not filtered_results)
        if filtered_results:
            results = filtered_results
        if scope_mismatch:
            scope_metrics_store.record_mismatch_detected(institution_id)
            logger.warning(
                "scope_mismatch_detected",
                tenant_id=institution_id,
                query_preview=query[:80],
                requested_scopes=list(requested_standards),
                original_result_count=len(output.get("results", []) or []),
                filtered_result_count=len(filtered_results),
                mismatch_blocked_count=1,
            )

        try:
            LeakCanary.verify_isolation(institution_id, results)
        except SecurityViolationError as e:
            logger.critical(
                "security_isolation_breach",
                error=str(e),
                institution_id=institution_id,
                mode="retrieval_router",
            )
            raise ApiError(
                status_code=500,
                code="SECURITY_ISOLATION_BREACH",
                message="Security isolation validation failed",
                details=str(e),
            )

        context_chunks = [r.get("content", "") for r in results if r.get("content")]
        context_map = {str(r.get("id")): r for r in results if r.get("id")}

        return {
            "context_chunks": context_chunks,
            "context_map": context_map,
            "citations": [str(r.get("id")) for r in results if r.get("id")],
            "mode": output.get("mode", "HYBRID"),
            "requested_scopes": list(requested_standards),
            "scope_mismatch_detected": scope_mismatch,
        }

    async def _get_context_vector_only(
        self,
        query: str,
        institution_id: str,
        k: int,
        container: Any,
        retrieval_request_id: str,
        requested_standards: tuple[str, ...] = (),
    ) -> GroundedContext:
        """Vector-only retrieval path."""
        retrieval_tools = container.retrieval_tools
        from app.core.middleware.security import LeakCanary

        # 1. Execute Retrieval
        scope: Dict[str, Any] = {"type": "institutional", "tenant_id": institution_id}
        if requested_standards:
            scope["filters"] = {
                "source_standards": list(requested_standards),
                "source_standard": requested_standards[0],
            }
        retrieve_start = perf_now()
        results = await retrieval_tools.retrieve(query=query, scope_context=scope, k=k)
        filtered_results = self._filter_results_by_scope(results, requested_standards)
        scope_mismatch = bool(requested_standards and results and not filtered_results)
        if filtered_results:
            results = filtered_results
        if scope_mismatch:
            scope_metrics_store.record_mismatch_detected(institution_id)
            logger.warning(
                "scope_mismatch_detected",
                tenant_id=institution_id,
                query_preview=query[:80],
                requested_scopes=list(requested_standards),
                original_result_count=len(results or []),
                filtered_result_count=len(filtered_results),
                mismatch_blocked_count=1,
            )

        logger.info(
            "retrieval_pipeline_timing",
            stage="vector_only_retrieve",
            retrieval_request_id=retrieval_request_id,
            tenant_id=institution_id,
            duration_ms=elapsed_ms(retrieve_start),
            result_count=len(results or []),
        )

        # 2. Security Validation (LeakCanary)
        try:
            LeakCanary.verify_isolation(institution_id, results)
        except SecurityViolationError as e:
            logger.critical(
                "security_isolation_breach",
                error=str(e),
                institution_id=institution_id,
                mode="vector_only",
            )
            raise ApiError(
                status_code=500,
                code="SECURITY_ISOLATION_BREACH",
                message="Security isolation validation failed",
                details=str(e),
            )

        # 3. Process results
        context_chunks = [r["content"] for r in results]
        context_map = {str(r.get("id")): r for r in results}

        return {
            "context_chunks": context_chunks,
            "context_map": context_map,
            "citations": [str(r.get("id")) for r in results if r.get("id")],
            "mode": "VECTOR_ONLY",
            "requested_scopes": list(requested_standards),
            "scope_mismatch_detected": scope_mismatch,
        }

    def _resolve_scope(self, query: str) -> dict[str, Any]:
        requested = self._scope_policy.extract_requested_scopes(query)
        ambiguous = self._scope_policy.has_ambiguous_reference(query) and not requested
        return {
            "requested_standards": requested,
            "requires_scope_clarification": ambiguous,
            "suggested_scopes": self._scope_policy.suggest_scope_candidates(query),
        }

    def _filter_results_by_scope(
        self, results: List[Dict[str, Any]], requested_standards: tuple[str, ...]
    ) -> List[Dict[str, Any]]:
        if not requested_standards:
            return results

        requested_upper = {item.upper() for item in requested_standards}
        filtered: List[Dict[str, Any]] = []
        for item in results:
            scope = self._scope_policy.extract_item_scope(item)
            if not scope:
                continue
            if any(target in scope.upper() for target in requested_upper):
                filtered.append(item)
        return filtered

    def optimize_context_for_prompt(
        self, context_chunks: List[str], context_map: Dict[str, Any]
    ) -> str:
        """
        Optimizes context by injecting global summaries if available.
        """
        global_summary = None
        if context_map:
            first_chunk = next(iter(context_map.values()), {})
            global_summary = first_chunk.get("metadata", {}).get(
                "global_summary"
            ) or first_chunk.get("source_metadata", {}).get("global_summary")

        context_text = "\n\n".join(context_chunks) if context_chunks else "General Knowledge"

        if global_summary:
            context_text = f"CONTEXTO DOCUMENTO: {global_summary}\n\n{context_text}"

        return context_text
