import structlog
from typing import Optional, Dict, Any
from fastapi import APIRouter, File, UploadFile, Form, Depends, Response
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

class CreateBatchRequest(BaseModel):
    tenant_id: str
    collection_key: str
    total_files: int
    auto_seal: bool = False
    collection_name: Optional[str] = None
    metadata: Dict[str, Any] = {}


# --- DEPENDENCIES ---

def get_batch_orchestrator(container=Depends(get_container)):
    return BatchOrchestrator(
        taxonomy_manager=TaxonomyRepository(),
        source_repo=container.source_repository
    )


# --- ENDPOINTS ---

@router.post("/batches")
async def create_ingestion_batch(
    request: CreateBatchRequest,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        result = await orchestrator.create_batch(
            tenant_id=tenant_id,
            collection_key=request.collection_key,
            total_files=request.total_files,
            auto_seal=request.auto_seal,
            collection_name=request.collection_name,
            metadata=request.metadata,
        )
        return {"status": "accepted", **result}
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        if "COLLECTION_SEALED" in detail:
            raise ApiError(
                status_code=409,
                code="COLLECTION_SEALED",
                message="Collection is sealed",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_BATCH_REQUEST",
            message="Invalid batch request",
            details=detail,
        )
    except Exception as e:
        logger.error("create_batch_failed", error=str(e), tenant_id=request.tenant_id)
        raise ApiError(status_code=500, code="CREATE_BATCH_FAILED", message="Create batch failed")


@router.post("/batches/{batch_id}/files")
async def add_file_to_batch(
    batch_id: str,
    response: Response,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        require_tenant_from_context()
        result = await orchestrator.add_file_to_batch(batch_id=batch_id, file=file, metadata=metadata)
        queue = result.get("queue")
        if isinstance(queue, dict):
            response.headers["X-Queue-Depth"] = str(int(queue.get("queue_depth") or 0))
            response.headers["X-Queue-ETA-Seconds"] = str(
                int(queue.get("estimated_wait_seconds") or 0)
            )
            max_pending = queue.get("max_pending")
            if max_pending is not None:
                response.headers["X-Queue-Max-Pending"] = str(int(max_pending))
        return {"status": "accepted", **result}
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        if detail in {"BATCH_NOT_FOUND", "COLLECTION_NOT_FOUND"}:
            raise ApiError(
                status_code=404,
                code=detail,
                message="Batch or collection not found",
                details=detail,
            )
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(
                status_code=429,
                code="INGESTION_BACKPRESSURE",
                message="Ingestion queue is saturated",
                details=detail,
            )
        if detail == "COLLECTION_SEALED" or detail == "BATCH_FILE_LIMIT_EXCEEDED":
            raise ApiError(
                status_code=409, code=detail, message="Batch cannot accept file", details=detail
            )
        raise ApiError(
            status_code=400,
            code="INVALID_BATCH_FILE_REQUEST",
            message="Invalid batch file request",
            details=detail,
        )
    except Exception as e:
        logger.error("add_file_to_batch_failed", batch_id=batch_id, error=str(e))
        detail = str(e)
        if "Bucket not found" in detail or "bucket" in detail.lower():
            raise ApiError(
                status_code=500,
                code="STORAGE_BUCKET_ERROR",
                message="Storage bucket error",
                details=detail,
            )
        raise ApiError(
            status_code=500, code="ADD_FILE_TO_BATCH_FAILED", message="Add file to batch failed"
        )


@router.post("/batches/{batch_id}/seal")
async def seal_ingestion_batch(
    batch_id: str,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        require_tenant_from_context()
        result = await orchestrator.seal_batch(batch_id=batch_id)
        return {"status": "sealed", **result}
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        if detail == "BATCH_NOT_FOUND":
            raise ApiError(
                status_code=404, code="BATCH_NOT_FOUND", message="Batch not found", details=detail
            )
        raise ApiError(
            status_code=400,
            code="INVALID_SEAL_BATCH_REQUEST",
            message="Invalid seal batch request",
            details=detail,
        )
    except Exception as e:
        logger.error("seal_batch_failed", batch_id=batch_id, error=str(e))
        raise ApiError(status_code=500, code="SEAL_BATCH_FAILED", message="Seal batch failed")
