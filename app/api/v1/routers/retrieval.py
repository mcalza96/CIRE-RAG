from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.schemas.retrieval_advanced import (
    ExplainRetrievalRequest,
    ExplainRetrievalResponse,
    HybridRetrievalRequest,
    HybridRetrievalResponse,
    MultiQueryRetrievalRequest,
    MultiQueryRetrievalResponse,
    ValidateScopeRequest,
    ValidateScopeResponse,
)
from app.api.v1.tenant_guard import enforce_tenant_match
from app.application.services.retrieval_contract_service import RetrievalContractService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/retrieval", tags=["retrieval"], dependencies=[Depends(require_service_auth)])


def get_retrieval_contract_service() -> RetrievalContractService:
    return RetrievalContractService()


@router.post(
    "/validate-scope",
    response_model=ValidateScopeResponse,
    responses={400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500]},
)
async def validate_scope(
    request: ValidateScopeRequest,
    service: RetrievalContractService = Depends(get_retrieval_contract_service),
) -> ValidateScopeResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return service.validate_scope(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_validate_scope_failed", error=str(exc))
        raise ApiError(status_code=500, code="SCOPE_VALIDATION_FAILED", message="Scope validation failed")


@router.post(
    "/hybrid",
    response_model=HybridRetrievalResponse,
    responses={400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500], 502: ERROR_RESPONSES[500]},
)
async def retrieval_hybrid(
    request: HybridRetrievalRequest,
    service: RetrievalContractService = Depends(get_retrieval_contract_service),
) -> HybridRetrievalResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return await service.run_hybrid(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("hybrid_retrieval_failed", error=str(exc))
        raise ApiError(status_code=500, code="HYBRID_RETRIEVAL_FAILED", message="Hybrid retrieval failed")


@router.post(
    "/multi-query",
    response_model=MultiQueryRetrievalResponse,
    responses={400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500], 502: ERROR_RESPONSES[500]},
)
async def retrieval_multi_query(
    request: MultiQueryRetrievalRequest,
    service: RetrievalContractService = Depends(get_retrieval_contract_service),
) -> MultiQueryRetrievalResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return await service.run_multi_query(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("multi_query_retrieval_failed", error=str(exc))
        raise ApiError(status_code=500, code="MULTI_QUERY_FAILED", message="Multi-query retrieval failed")


@router.post(
    "/explain",
    response_model=ExplainRetrievalResponse,
    responses={400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500], 502: ERROR_RESPONSES[500]},
)
async def retrieval_explain(
    request: ExplainRetrievalRequest,
    service: RetrievalContractService = Depends(get_retrieval_contract_service),
) -> ExplainRetrievalResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return await service.run_explain(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_explain_failed", error=str(exc))
        raise ApiError(status_code=500, code="RETRIEVAL_EXPLAIN_FAILED", message="Retrieval explain failed")
