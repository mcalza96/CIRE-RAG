from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.schemas.retrieval_advanced import (
    ComprehensiveRetrievalRequest,
    ComprehensiveRetrievalResponse,
    ExplainRetrievalRequest,
    ExplainRetrievalResponse,
    ValidateScopeRequest,
    ValidateScopeResponse,
)
from app.api.v1.tenant_guard import enforce_tenant_match
from app.services.retrieval.orchestration.contract_manager import ContractManager
from app.api.dependencies import get_container

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/retrieval", tags=["retrieval"], dependencies=[Depends(require_service_auth)]
)


def get_contract_manager(container=Depends(get_container)) -> ContractManager:
    return ContractManager(knowledge_service=container.knowledge_service)


@router.post(
    "/validate-scope",
    response_model=ValidateScopeResponse,
    responses={400: ERROR_RESPONSES[400], 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500]},
)
async def validate_scope(
    request: ValidateScopeRequest,
    service: ContractManager = Depends(get_contract_manager),
) -> ValidateScopeResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return service.validate_scope(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_validate_scope_failed", error=str(exc), exc_info=True)
        raise ApiError(
            status_code=500, code="SCOPE_VALIDATION_FAILED", message="Scope validation failed"
        )


@router.post(
    "/comprehensive",
    response_model=ComprehensiveRetrievalResponse,
    responses={
        400: ERROR_RESPONSES[400],
        401: ERROR_RESPONSES[401],
        500: ERROR_RESPONSES[500],
        502: ERROR_RESPONSES[500],
    },
)
async def retrieval_comprehensive(
    request: ComprehensiveRetrievalRequest,
    service: ContractManager = Depends(get_contract_manager),
) -> ComprehensiveRetrievalResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return await service.run_comprehensive(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("comprehensive_retrieval_failed", error=str(exc), exc_info=True)
        raise ApiError(
            status_code=500,
            code="COMPREHENSIVE_RETRIEVAL_FAILED",
            message="Comprehensive retrieval failed",
        )


@router.post(
    "/explain",
    response_model=ExplainRetrievalResponse,
    responses={
        400: ERROR_RESPONSES[400],
        401: ERROR_RESPONSES[401],
        500: ERROR_RESPONSES[500],
        502: ERROR_RESPONSES[500],
    },
)
async def retrieval_explain(
    request: ExplainRetrievalRequest,
    service: ContractManager = Depends(get_contract_manager),
) -> ExplainRetrievalResponse:
    tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
    normalized_request = request.model_copy(update={"tenant_id": tenant_id})
    try:
        return await service.run_explain(normalized_request)
    except ApiError:
        raise
    except Exception as exc:
        logger.error("retrieval_explain_failed", error=str(exc), exc_info=True)
        raise ApiError(
            status_code=500, code="RETRIEVAL_EXPLAIN_FAILED", message="Retrieval explain failed"
        )
