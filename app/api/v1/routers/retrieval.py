from typing import Any, Dict, Optional

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.v1.errors import ApiError
from app.infrastructure.container import CognitiveContainer

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/retrieval", tags=["retrieval"])


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


@router.post("/chunks", response_model=Dict[str, Any])
async def retrieve_chunks(request: RetrievalRequest):
    try:
        if not request.tenant_id:
            raise ApiError(status_code=400, code="TENANT_ID_REQUIRED", message="tenant_id is required")

        scope_context: Dict[str, Any] = {"type": "institutional", "tenant_id": request.tenant_id}
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
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_chunks_failed", error=str(exc))
        raise ApiError(status_code=500, code="RETRIEVAL_CHUNKS_FAILED", message="Retrieval chunks failed")


@router.post("/summaries", response_model=Dict[str, Any])
async def retrieve_summaries(request: SummaryRequest):
    try:
        if not request.tenant_id:
            raise ApiError(status_code=400, code="TENANT_ID_REQUIRED", message="tenant_id is required")

        container = CognitiveContainer.get_instance()
        rows = await container.retrieval_tools.retrieve_summaries(
            query=request.query,
            tenant_id=request.tenant_id,
            k=max(1, int(request.summary_k)),
            collection_id=request.collection_id,
        )

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
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_summaries_failed", error=str(exc))
        raise ApiError(status_code=500, code="RETRIEVAL_SUMMARIES_FAILED", message="Retrieval summaries failed")
