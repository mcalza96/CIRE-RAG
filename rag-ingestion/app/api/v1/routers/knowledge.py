import structlog
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from app.domain.knowledge_schemas import RetrievalIntent, GroundedContext
from app.services.knowledge.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["knowledge"])

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
        context = await service.get_grounded_context(
            query=intent.query,
            institution_id=intent.tenant_id
        )
        return context
    except Exception as e:
        logger.error("retrieval_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Retrieval failed: {str(e)}")
