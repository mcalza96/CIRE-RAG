from typing import Any, Dict

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.tenant_guard import enforce_tenant_match
from app.api.v1.routers.ingestion import get_ingestion_use_case
from app.infrastructure.observability.retrieval_metrics import retrieval_metrics_store
from app.application.use_cases.manual_ingestion_use_case import ManualIngestionUseCase

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/management", tags=["management"], dependencies=[Depends(require_service_auth)])


class CollectionsResponse(BaseModel):
    items: list[Dict[str, Any]]


class TenantsResponse(BaseModel):
    items: list[Dict[str, Any]]


class QueueSnapshot(BaseModel):
    queue_depth: int = Field(default=0, examples=[2])
    max_pending: int | None = Field(default=None, examples=[500])
    estimated_wait_seconds: int = Field(default=0, examples=[40])


class QueueStatusResponse(BaseModel):
    status: str = Field(examples=["ok"])
    tenant_id: str = Field(examples=["tenant-demo"])
    queue: QueueSnapshot


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    service: str = Field(examples=["rag-engine"])
    api_v1: str = Field(examples=["available"])


class RetrievalMetricsResponse(BaseModel):
    hybrid_rpc_hits: int = 0
    hybrid_rpc_fallbacks: int = 0
    hybrid_rpc_disabled: int = 0
    hybrid_rpc_hit_ratio: float = 0.0
    rpc_contract_status: str = "unknown"
    rpc_contract_mismatch_events: int = 0


@router.get(
    "/tenants",
    operation_id="listAvailableTenants",
    summary="List available tenants",
    description=(
        "Returns available tenants for authenticated service callers. "
        "This route does not require X-Tenant-ID header."
    ),
    response_model=TenantsResponse,
    responses={
        200: {
            "description": "Available tenants",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {"id": "tenant-demo", "name": "Tenant Demo", "created_at": "2026-02-10T12:00:00Z"}
                        ]
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        500: ERROR_RESPONSES[500],
    },
)
async def list_tenants(
    limit: int = Query(default=200, ge=1, le=1000),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
) -> TenantsResponse:
    try:
        return TenantsResponse(items=await use_case.list_tenants(limit=limit))
    except Exception as e:
        logger.error("management_list_tenants_failed", error=str(e), limit=limit)
        raise ApiError(status_code=500, code="TENANT_LIST_FAILED", message="Failed to list tenants")


@router.get(
    "/collections",
    operation_id="listTenantCollections",
    summary="List tenant collections",
    description="Returns available knowledge collections for a tenant.",
    response_model=CollectionsResponse,
    responses={
        200: {
            "description": "Collections for tenant",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": "d8e84f68-0eb4-4f09-8d4c-714d1966de7f",
                                "collection_key": "iso-9001",
                                "name": "ISO 9001",
                            }
                        ]
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
async def list_collections(
    tenant_id: str = Query(..., examples=["tenant-demo"]),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
) -> CollectionsResponse:
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        return CollectionsResponse(items=await use_case.list_collections(tenant_id=tenant_ctx))
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail)
        raise ApiError(status_code=400, code="INVALID_TENANT_ID", message="Invalid tenant id", details=detail)
    except Exception as e:
        logger.error("management_list_collections_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(status_code=500, code="COLLECTION_LIST_FAILED", message="Failed to list collections")


@router.get(
    "/queue/status",
    operation_id="getTenantQueueStatus",
    summary="Get tenant queue status",
    description="Returns ingestion queue depth and estimated wait time for a tenant.",
    response_model=QueueStatusResponse,
    responses={
        200: {
            "description": "Queue status for tenant",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "tenant_id": "tenant-demo",
                        "queue": {"queue_depth": 2, "max_pending": 500, "estimated_wait_seconds": 40},
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
async def get_queue_status(
    tenant_id: str = Query(..., examples=["tenant-demo"]),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
) -> QueueStatusResponse:
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        queue = await use_case.get_queue_status(tenant_id=tenant_ctx)
        return QueueStatusResponse(
            status="ok",
            tenant_id=tenant_ctx,
            queue=QueueSnapshot(
                queue_depth=int(queue.get("queue_depth") or 0),
                max_pending=queue.get("max_pending"),
                estimated_wait_seconds=int(queue.get("estimated_wait_seconds") or 0),
            ),
        )
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail)
        raise ApiError(status_code=400, code="INVALID_TENANT_ID", message="Invalid tenant id", details=detail)
    except Exception as e:
        logger.error("management_queue_status_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(status_code=500, code="QUEUE_STATUS_FAILED", message="Failed to get queue status")


@router.get(
    "/health",
    operation_id="getManagementHealth",
    summary="Get management health",
    description="Returns lightweight health status for API v1 management domain.",
    response_model=HealthResponse,
    responses={
        200: {
            "description": "API v1 health",
            "content": {
                "application/json": {
                    "example": {"status": "ok", "service": "rag-engine", "api_v1": "available"}
                }
            },
        },
        401: ERROR_RESPONSES[401],
        500: ERROR_RESPONSES[500],
    },
)
async def management_health() -> HealthResponse:
    return HealthResponse(status="ok", service="rag-engine", api_v1="available")


@router.get(
    "/retrieval/metrics",
    operation_id="getRetrievalMetrics",
    summary="Get retrieval backend metrics",
    description="Returns runtime counters for hybrid SQL RPC usage and fallback behavior.",
    response_model=RetrievalMetricsResponse,
    responses={200: {"description": "Retrieval backend metrics"}, 401: ERROR_RESPONSES[401], 500: ERROR_RESPONSES[500]},
)
async def get_retrieval_metrics() -> RetrievalMetricsResponse:
    return RetrievalMetricsResponse.model_validate(retrieval_metrics_store.snapshot())
