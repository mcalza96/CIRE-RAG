import structlog
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from app.domain.knowledge_schemas import RetrievalIntent
from app.services.knowledge.knowledge_service import KnowledgeService
from app.services.knowledge.grounded_answer_service import GroundedAnswerService

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
        if not intent.tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id is required")
        tenant_id = str(intent.tenant_id)
        context = await service.get_grounded_context(
            query=intent.query,
            institution_id=tenant_id
        )
        return context
    except HTTPException:
        raise
    except Exception as e:
        logger.error("retrieval_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Retrieval failed: {str(e)}")


@router.post("/answer", response_model=Dict[str, Any])
async def answer_with_grounding(
    intent: RetrievalIntent,
    knowledge_service: KnowledgeService = Depends(KnowledgeService),
    answer_service: GroundedAnswerService = Depends(GroundedAnswerService),
):
    """Retrieve context and generate final grounded answer."""
    try:
        if not intent.tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id is required")
        tenant_id = str(intent.tenant_id)
        grounded = await knowledge_service.get_grounded_context(
            query=intent.query,
            institution_id=tenant_id,
        )
        context_chunks = grounded.get("context_chunks", []) or []
        answer = await answer_service.generate_answer(query=intent.query, context_chunks=context_chunks)
        return {
            "answer": answer,
            "context_chunks": context_chunks,
            "citations": grounded.get("citations", []),
            "mode": grounded.get("mode", "HYBRID"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("grounded_answer_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Grounded answer failed: {str(e)}")
