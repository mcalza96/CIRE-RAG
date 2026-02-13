from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.api.v1.auth import require_service_auth
from app.api.v1.routers.retrieval import (
    RetrievalRequest,
    SummaryRequest,
    retrieve_chunks,
    retrieve_summaries,
)

router = APIRouter(prefix="/debug/retrieval", tags=["debug-retrieval"], dependencies=[Depends(require_service_auth)])


@router.post("/chunks", response_model=Dict[str, Any])
async def debug_retrieve_chunks(request: RetrievalRequest):
    return await retrieve_chunks(request)


@router.post("/summaries", response_model=Dict[str, Any])
async def debug_retrieve_summaries(request: SummaryRequest):
    return await retrieve_summaries(request)
