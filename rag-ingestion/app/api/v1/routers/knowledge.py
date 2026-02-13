import structlog
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.domain.knowledge_schemas import RetrievalIntent
from app.services.knowledge.knowledge_service import KnowledgeService
from app.services.knowledge.grounded_answer_service import GroundedAnswerService
from app.core.observability.scope_metrics import scope_metrics_store

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
        if grounded.get("requires_scope_clarification"):
            logger.info(
                "scope_clarification_returned",
                tenant_id=tenant_id,
                scope_candidates=grounded.get("scope_candidates", []),
            )
            return {
                "answer": grounded.get("scope_message"),
                "context_chunks": [],
                "citations": [],
                "mode": grounded.get("mode", "AMBIGUOUS_SCOPE"),
                "scope_candidates": grounded.get("scope_candidates", []),
            }

        context_chunks = grounded.get("context_chunks", []) or []
        if grounded.get("scope_mismatch_detected"):
            scope_metrics_store.record_mismatch_blocked(tenant_id)
            logger.warning(
                "scope_mismatch_blocked_answer",
                tenant_id=tenant_id,
                requested_scopes=grounded.get("requested_scopes", []),
                mismatch_blocked_count=1,
            )
            return {
                "answer": (
                    "⚠️ Se detectó inconsistencia de ámbito entre la pregunta y las fuentes recuperadas. "
                    "Reformula indicando explícitamente la norma objetivo."
                ),
                "context_chunks": context_chunks,
                "citations": grounded.get("citations", []),
                "mode": grounded.get("mode", "HYBRID"),
            }

        answer = await answer_service.generate_answer(query=intent.query, context_chunks=context_chunks)
        return {
            "answer": answer,
            "context_chunks": context_chunks,
            "citations": grounded.get("citations", []),
            "mode": grounded.get("mode", "HYBRID"),
            "requested_scopes": grounded.get("requested_scopes", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("grounded_answer_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Grounded answer failed: {str(e)}")


@router.get("/scope-health", response_model=Dict[str, Any])
async def scope_health(tenant_id: Optional[str] = Query(default=None)):
    """Returns in-memory scope safety KPIs."""
    return scope_metrics_store.snapshot(tenant_id=tenant_id)
