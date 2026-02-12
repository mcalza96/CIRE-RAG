from typing import List, Dict, Any
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
        logger.info(
            "Retrieving grounded context",
            query_preview=query[:50],
            institution_id=institution_id,
            mode="tricameral" if self._use_tricameral() else "vector_only"
        )
        
        from app.infrastructure.container import CognitiveContainer
        container = CognitiveContainer.get_instance()

        if self._use_tricameral():
            return await self._get_context_tricameral(query, institution_id, k, container)

        return await self._get_context_vector_only(query, institution_id, k, container)

    @staticmethod
    def _use_tricameral() -> bool:
        return settings.USE_TRICAMERAL

    async def _get_context_tricameral(
        self,
        query: str,
        institution_id: str,
        k: int,
        container: Any,
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
            metadata={},
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
        self, query: str, institution_id: str, k: int, container: Any
    ) -> Dict[str, Any]:
        """Vector-only retrieval path."""
        retrieval_tools = container.retrieval_tools
        from app.core.middleware.security import LeakCanary

        # 1. Execute Retrieval
        scope = {"type": "institutional", "tenant_id": institution_id}
        results = await retrieval_tools.retrieve(
            query=query,
            scope_context=scope,
            k=k
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
