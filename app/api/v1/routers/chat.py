from typing import Any, Dict, List, Optional
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.tenant_guard import enforce_tenant_match
from app.infrastructure.dependencies import get_knowledge_service
from app.services.knowledge.knowledge_service import KnowledgeService

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_service_auth)])


class ChatMessage(BaseModel):
    role: str
    content: str

    model_config = {
        "json_schema_extra": {
            "example": {"role": "user", "content": "Que exige la clausula 8.5 de ISO 9001?"}
        }
    }


class ChatCompletionRequest(BaseModel):
    message: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    history: List[ChatMessage] = Field(default_factory=list)
    max_context_chunks: int = 10

    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "Que exige la clausula 8.5 de ISO 9001?",
                "tenant_id": "tenant-demo",
                "history": [{"role": "user", "content": "Resume la clausula 8"}],
                "max_context_chunks": 8,
            }
        }
    }


class ChatCompletionResponse(BaseModel):
    interaction_id: str
    query: str
    context_chunks: List[str]
    context_map: Dict[str, Any]
    citations: List[str]
    mode: str
    requires_scope_clarification: bool = False
    scope_warnings: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "interaction_id": "be6236d6-5eca-4ec6-8296-5d42c4b16595",
                "query": "Que exige la clausula 8.5 de ISO 9001?",
                "context_chunks": ["La organizacion debe implementar controles de produccion..."],
                "context_map": {"chunk-001": {"id": "chunk-001", "similarity": 0.87}},
                "citations": ["chunk-001", "chunk-017"],
                "mode": "VECTOR_ONLY",
                "requires_scope_clarification": False,
                "scope_warnings": None,
            }
        }
    }


class ChatFeedbackRequest(BaseModel):
    interaction_id: str
    rating: str
    comment: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "interaction_id": "be6236d6-5eca-4ec6-8296-5d42c4b16595",
                "rating": "up",
                "comment": "Respuesta precisa y bien citada",
            }
        }
    }


def _build_retrieval_query(message: str, history: List[ChatMessage], max_turns: int = 6) -> str:
    question = (message or "").strip()
    if not history:
        return question

    history_tail = [m for m in history if (m.content or "").strip()][-max_turns:]
    if not history_tail:
        return question

    rendered_history = "\n".join(
        f"{(m.role or 'user').upper()}: {m.content.strip()}" for m in history_tail
    )
    return f"HISTORIAL RELEVANTE:\n{rendered_history}\n\nPREGUNTA ACTUAL:\n{question}"


@router.post(
    "/completions",
    operation_id="createChatCompletion",
    summary="Create grounded chat completion",
    description=(
        "Retrieval-only endpoint. Returns grounded evidence payload for external orchestrators. "
        "Authentication: Bearer token or X-Service-Secret (same shared secret)."
    ),
    response_model=ChatCompletionResponse,
    responses={
        200: {
            "description": "Grounded answer with citations",
            "content": {
                "application/json": {
                    "example": {
                        "interaction_id": "be6236d6-5eca-4ec6-8296-5d42c4b16595",
                        "query": "Que exige la clausula 8.5 de ISO 9001?",
                        "context_chunks": [
                            "La organizacion debe implementar controles de produccion..."
                        ],
                        "context_map": {"chunk-001": {"id": "chunk-001", "similarity": 0.87}},
                        "citations": ["chunk-001", "chunk-017"],
                        "mode": "VECTOR_ONLY",
                        "requires_scope_clarification": False,
                        "scope_warnings": None,
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        400: ERROR_RESPONSES[400],
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
)
async def create_chat_completion(
    request: ChatCompletionRequest,
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> ChatCompletionResponse:
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        retrieval_query = _build_retrieval_query(request.message, request.history)
        context = await knowledge_service.get_grounded_context(
            query=retrieval_query,
            institution_id=tenant_id,
        )

        requires_scope = bool(context.get("requires_scope_clarification"))
        if requires_scope:
            return ChatCompletionResponse(
                interaction_id=str(uuid4()),
                query=request.message,
                context_chunks=[],
                context_map={},
                citations=[],
                mode=str(context.get("mode") or "AMBIGUOUS_SCOPE"),
                requires_scope_clarification=True,
                scope_warnings=str(context.get("scope_message") or ""),
            )

        context_chunks = list(context.get("context_chunks") or [])
        if len(context_chunks) > max(1, int(request.max_context_chunks)):
            context_chunks = context_chunks[: max(1, int(request.max_context_chunks))]

        return ChatCompletionResponse(
            interaction_id=str(uuid4()),
            query=request.message,
            context_chunks=context_chunks,
            context_map=dict(context.get("context_map") or {}),
            citations=[str(c) for c in (context.get("citations") or [])],
            mode=str(context.get("mode") or "VECTOR_ONLY"),
            requires_scope_clarification=False,
            scope_warnings=context.get("scope_message"),
        )
    except ApiError:
        raise
    except Exception as e:
        logger.error("chat_completion_failed", error=str(e), tenant_id=request.tenant_id)
        raise ApiError(
            status_code=500, code="CHAT_COMPLETION_FAILED", message="Chat completion failed"
        )


@router.post(
    "/feedback",
    operation_id="submitChatFeedback",
    summary="Submit chat feedback",
    description="Stores user feedback for a generated chat interaction. Requires service auth.",
    responses={
        200: {
            "description": "Feedback accepted",
            "content": {
                "application/json": {
                    "example": {
                        "status": "accepted",
                        "interaction_id": "be6236d6-5eca-4ec6-8296-5d42c4b16595",
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        400: ERROR_RESPONSES[400],
        422: ERROR_RESPONSES[422],
        500: ERROR_RESPONSES[500],
    },
)
async def submit_chat_feedback(request: ChatFeedbackRequest) -> Dict[str, Any]:
    logger.info(
        "chat_feedback_received",
        interaction_id=request.interaction_id,
        rating=request.rating,
        has_comment=bool(request.comment),
    )
    return {"status": "accepted", "interaction_id": request.interaction_id}
