import structlog
from typing import Dict, Any
from fastapi import APIRouter, Depends
from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match
from app.domain.knowledge_schemas import RetrievalIntent
from app.services.knowledge.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["knowledge"], dependencies=[Depends(require_service_auth)])

@router.post("/retrieve", response_model=Dict[str, Any])
async def retrieve_knowledge(
    intent: RetrievalIntent,
    service: KnowledgeService = Depends(KnowledgeService)
):
    """
    Execute a Basic Knowledge Search based on the provided intent.
    Refactored to return raw context chunks (Tricameral logic moved to audit-engine).
    """
    try:
        tenant_id = enforce_tenant_match(str(intent.tenant_id or ""), "body.tenant_id")
        context = await service.get_grounded_context(
            query=intent.query,
            institution_id=tenant_id
        )
        if context.get("requires_scope_clarification"):
            return {
                "context_chunks": [],
                "context_map": {},
                "citations": [],
                "mode": context.get("mode", "AMBIGUOUS_SCOPE"),
                "scope_candidates": context.get("scope_candidates", []),
                "scope_message": context.get("scope_message"),
            }
        return context
    except ApiError:
        raise
    except Exception as e:
        logger.error("retrieval_failed", error=str(e))
        raise ApiError(status_code=500, code="KNOWLEDGE_RETRIEVAL_FAILED", message="Retrieval failed", details=str(e))
