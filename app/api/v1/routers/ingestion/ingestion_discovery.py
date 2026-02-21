import structlog
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match, require_tenant_from_context
from app.workflows.ingestion.batch_orchestrator import BatchOrchestrator
from app.api.dependencies import get_container
from app.infrastructure.supabase.repositories.taxonomy_repository import TaxonomyRepository

logger = structlog.get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_service_auth)])

# --- SCHEMAS ---

class CleanupCollectionRequest(BaseModel):
    tenant_id: str
    collection_key: str


# --- DEPENDENCIES ---

def get_batch_orchestrator(container=Depends(get_container)):
    return BatchOrchestrator(
        taxonomy_manager=TaxonomyRepository(),
        source_repo=container.source_repository
    )


# --- ENDPOINTS ---

@router.get("/documents")
async def list_documents(
    limit: int = 20,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator)
):
    """
    List registered source documents from Supabase.
    """
    try:
        require_tenant_from_context()
        docs = await orchestrator.query_service.list_recent_documents(limit)
        return docs
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if detail == "TENANT_CONTEXT_REQUIRED":
            raise ApiError(
                status_code=400,
                code="TENANT_HEADER_REQUIRED",
                message="Missing tenant context",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_DOCUMENT_LIST_REQUEST",
            message="Invalid document list request",
            details=detail,
        )
    except Exception as e:
        logger.error("list_documents_failed", error=str(e))
        raise ApiError(
            status_code=500, code="DOCUMENT_LIST_FAILED", message="Failed to list documents"
        )


@router.get("/collections")
async def list_collections(
    tenant_id: str,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        return await orchestrator.query_service.list_collections(tenant_id=str(tenant_ctx))
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        raise ApiError(
            status_code=400, code="INVALID_TENANT_ID", message="Invalid tenant id", details=detail
        )
    except Exception as e:
        logger.error("list_collections_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(
            status_code=500, code="COLLECTION_LIST_FAILED", message="Failed to list collections"
        )


@router.post("/collections/cleanup")
async def cleanup_collection(
    request: CleanupCollectionRequest,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        return await orchestrator.query_service.cleanup_collection(
            tenant_id=tenant_id,
            collection_key=request.collection_key,
        )
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        if detail == "COLLECTION_NOT_FOUND":
            raise ApiError(
                status_code=404,
                code="COLLECTION_NOT_FOUND",
                message="Collection not found",
                details=detail,
            )
        if detail == "COLLECTION_SEALED":
            raise ApiError(
                status_code=409,
                code="COLLECTION_SEALED",
                message="Collection is sealed",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_COLLECTION_CLEANUP",
            message="Invalid cleanup request",
            details=detail,
        )
    except Exception as e:
        logger.error(
            "cleanup_collection_failed",
            tenant_id=request.tenant_id,
            collection_key=request.collection_key,
            error=str(e),
        )
        raise ApiError(
            status_code=500, code="CLEANUP_COLLECTION_FAILED", message="Cleanup collection failed"
        )
