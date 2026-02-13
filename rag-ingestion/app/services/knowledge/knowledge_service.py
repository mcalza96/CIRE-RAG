import time
from typing import List, Dict, Any
from uuid import uuid4
import structlog
from app.core.retrieval_config import retrieval_settings
from app.core.settings import settings
from app.application.services.tricameral_orchestrator import TricameralOrchestrator
from app.domain.knowledge_schemas import RetrievalIntent, AgentRole, TaskType

logger = structlog.get_logger(__name__)

class KnowledgeService:
    """
    Application Service for Basic Knowledge Retrieval.
    Refactored to remove tricameral logic (moved to audit-engine).
    """

    async def get_grounded_context(
        self, 
        query: str, 
        institution_id: str, 
        k: int = retrieval_settings.TOP_K
    ) -> Dict[str, Any]:
        """
        Retrieves context via vector search.
        """
        retrieval_request_id = str(uuid4())
        start = time.perf_counter()
        selected_mode = "tricameral" if self._use_tricameral() else "vector_only"

        logger.info(
            "Retrieving grounded context",
            query_preview=query[:50],
            institution_id=institution_id,
            mode=selected_mode,
            retrieval_request_id=retrieval_request_id,
        )
        
        from app.infrastructure.container import CognitiveContainer
        container = CognitiveContainer.get_instance()

        if self._use_tricameral():
            result = await self._get_context_tricameral(
                query,
                institution_id,
                k,
                container,
                retrieval_request_id,
            )
        else:
            result = await self._get_context_vector_only(
                query,
                institution_id,
                k,
                container,
                retrieval_request_id,
            )

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "retrieval_pipeline_timing",
            stage="pipeline_total",
            mode=selected_mode,
            retrieval_request_id=retrieval_request_id,
            tenant_id=institution_id,
            duration_ms=elapsed_ms,
            result_chunks=len(result.get("context_chunks", []) or []),
        )
        return result

    @staticmethod
    def _use_tricameral() -> bool:
        return settings.USE_TRICAMERAL

    async def _get_context_tricameral(
        self,
        query: str,
        institution_id: str,
        k: int,
        container: Any,
        retrieval_request_id: str,
    ) -> Dict[str, Any]:
        from app.core.middleware.security import LeakCanary

        orchestrator = TricameralOrchestrator(
            vector_tools=container.retrieval_tools,
        )

        intent = RetrievalIntent(
            query=query,
            tenant_id=institution_id,
            role=AgentRole.SOCRATIC_MENTOR,
            task=TaskType.EXPLANATION,
            metadata={"retrieval_request_id": retrieval_request_id},
        )

        output = await orchestrator.orchestrate(intent=intent, k=k)
        results = output.get("results", []) or []

        try:
            LeakCanary.verify_isolation(institution_id, results)
        except Exception as e:
            logger.error("Security isolation breach detected (tricameral)", error=str(e), institution_id=institution_id)
            return {"context_chunks": [], "context_map": {}, "citations": [], "mode": output.get("mode")}

        context_chunks = [r.get("content", "") for r in results if r.get("content")]
        context_map = {str(r.get("id")): r for r in results if r.get("id")}

        return {
            "context_chunks": context_chunks,
            "context_map": context_map,
            "citations": output.get("citations", []),
            "mode": output.get("mode", "HYBRID"),
        }

    async def _get_context_vector_only(
        self,
        query: str,
        institution_id: str,
        k: int,
        container: Any,
        retrieval_request_id: str,
    ) -> Dict[str, Any]:
        """Vector-only retrieval path."""
        retrieval_tools = container.retrieval_tools
        from app.core.middleware.security import LeakCanary

        # 1. Execute Retrieval
        scope = {"type": "institutional", "tenant_id": institution_id}
        retrieve_start = time.perf_counter()
        results = await retrieval_tools.retrieve(
            query=query,
            scope_context=scope,
            k=k
        )

        logger.info(
            "retrieval_pipeline_timing",
            stage="vector_only_retrieve",
            retrieval_request_id=retrieval_request_id,
            tenant_id=institution_id,
            duration_ms=round((time.perf_counter() - retrieve_start) * 1000, 2),
            result_count=len(results or []),
        )
        
        # 2. Security Validation (LeakCanary)
        try:
            LeakCanary.verify_isolation(institution_id, results)
        except Exception as e:
            logger.error("Security isolation breach detected", error=str(e), institution_id=institution_id)
            return {"context_chunks": [], "context_map": {}}

        # 3. Process results
        context_chunks = [r["content"] for r in results]
        context_map = {str(r.get("id")): r for r in results}

        return {
            "context_chunks": context_chunks, 
            "context_map": context_map
        }

    def optimize_context_for_prompt(self, context_chunks: List[str], context_map: Dict[str, Any]) -> str:
        """
        Optimizes context by injecting global summaries if available.
        """
        global_summary = None
        if context_map:
            first_chunk = next(iter(context_map.values()), {})
            global_summary = (
                first_chunk.get("metadata", {}).get("global_summary") or 
                first_chunk.get("source_metadata", {}).get("global_summary")
            )

        context_text = "\n\n".join(context_chunks) if context_chunks else "General Knowledge"
        
        if global_summary:
            context_text = f"CONTEXTO DOCUMENTO: {global_summary}\n\n{context_text}"
            
        return context_text
