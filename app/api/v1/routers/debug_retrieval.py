from typing import Any, Dict, Optional

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match
from app.core.middleware.security import LeakCanary, SecurityViolationError
from app.infrastructure.container import CognitiveContainer

router = APIRouter(prefix="/debug/retrieval", tags=["debug-retrieval"], dependencies=[Depends(require_service_auth)])
logger = structlog.get_logger(__name__)


class RetrievalRequest(BaseModel):
    query: str
    tenant_id: str
    collection_id: Optional[str] = None
    chunk_k: int = 12
    fetch_k: int = 60


class SummaryRequest(BaseModel):
    query: str
    tenant_id: str
    collection_id: Optional[str] = None
    summary_k: int = 5


async def retrieve_chunks(request: RetrievalRequest) -> Dict[str, Any]:
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")

        scope_context: Dict[str, Any] = {"type": "institutional", "tenant_id": tenant_id}
        if request.collection_id:
            scope_context["filters"] = {"collection_id": request.collection_id}

        container = CognitiveContainer.get_instance()
        rows = await container.retrieval_tools.retrieve(
            query=request.query,
            scope_context=scope_context,
            k=max(1, int(request.chunk_k)),
            fetch_k=max(1, int(request.fetch_k)),
            enable_reranking=True,
        )
        LeakCanary.verify_isolation(tenant_id, rows)

        items = [
            {
                "source": f"C{i+1}",
                "content": str(row.get("content") or "").strip(),
                "score": float(row.get("similarity") or 0.0),
                "metadata": {"row": row},
            }
            for i, row in enumerate(rows)
            if row.get("content")
        ]
        return {"items": items}
    except SecurityViolationError as exc:
        logger.critical(
            "security_isolation_breach",
            endpoint="/debug/retrieval/chunks",
            tenant_id=request.tenant_id,
            error=str(exc),
        )
        raise ApiError(
            status_code=500,
            code="SECURITY_ISOLATION_BREACH",
            message="Security isolation validation failed",
            details=str(exc),
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_chunks_failed", endpoint="/debug/retrieval/chunks", error=str(exc))
        raise ApiError(status_code=500, code="RETRIEVAL_CHUNKS_FAILED", message="Retrieval chunks failed")


async def retrieve_summaries(request: SummaryRequest) -> Dict[str, Any]:
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")

        container = CognitiveContainer.get_instance()
        rows = await container.retrieval_tools.retrieve_summaries(
            query=request.query,
            tenant_id=tenant_id,
            k=max(1, int(request.summary_k)),
            collection_id=request.collection_id,
        )
        LeakCanary.verify_isolation(tenant_id, rows)

        items = [
            {
                "source": f"R{i+1}",
                "content": str(row.get("content") or "").strip(),
                "score": float(row.get("similarity") or 0.0),
                "metadata": {"row": row},
            }
            for i, row in enumerate(rows)
            if row.get("content")
        ]
        return {"items": items}
    except SecurityViolationError as exc:
        logger.critical(
            "security_isolation_breach",
            endpoint="/debug/retrieval/summaries",
            tenant_id=request.tenant_id,
            error=str(exc),
        )
        raise ApiError(
            status_code=500,
            code="SECURITY_ISOLATION_BREACH",
            message="Security isolation validation failed",
            details=str(exc),
        )
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_summaries_failed", endpoint="/debug/retrieval/summaries", error=str(exc))
        raise ApiError(status_code=500, code="RETRIEVAL_SUMMARIES_FAILED", message="Retrieval summaries failed")


@router.post("/chunks", response_model=Dict[str, Any])
async def debug_retrieve_chunks(request: RetrievalRequest):
    return await retrieve_chunks(request)


@router.post("/summaries", response_model=Dict[str, Any])
async def debug_retrieve_summaries(request: SummaryRequest):
    return await retrieve_summaries(request)
