import structlog
import asyncio
import json
import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, File, UploadFile, Form, Depends, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match, require_tenant_from_context
from app.application.use_cases.manual_ingestion_use_case import ManualIngestionUseCase
from app.application.use_cases.institutional_ingestion_use_case import InstitutionalIngestionUseCase
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.services.embedding_service import JinaEmbeddingService
from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/ingestion", tags=["ingestion"], dependencies=[Depends(require_service_auth)]
)

# --- SCHEMAS ---


class EmbedRequest(BaseModel):
    texts: List[str]
    task: Optional[str] = "retrieval.passage"
    provider: Optional[str] = None
    mode: Optional[str] = None


class InstitutionalIngestionRequest(BaseModel):
    file_path: str
    tenant_id: str
    document_id: str
    scope: str = "private"
    metadata: Dict[str, Any] = {}


class CreateBatchRequest(BaseModel):
    tenant_id: str
    collection_key: str
    total_files: int
    auto_seal: bool = False
    collection_name: Optional[str] = None
    metadata: Dict[str, Any] = {}


class CleanupCollectionRequest(BaseModel):
    tenant_id: str
    collection_key: str


class ReplayEnrichmentRequest(BaseModel):
    include_visual: bool = True
    include_graph: bool = True
    include_raptor: bool = True


# --- DEPENDENCIES ---


def get_dispatcher():
    repo = SupabaseContentRepository()
    return IngestionDispatcher(repo)


def get_ingestion_use_case(dispatcher: IngestionDispatcher = Depends(get_dispatcher)):
    return ManualIngestionUseCase(dispatcher)


def get_institutional_use_case():
    return InstitutionalIngestionUseCase()


# --- ENDPOINTS ---


@router.post("/embed")
async def embed_texts(request: EmbedRequest):
    """
    Fast Inference Endpoint for text embedding.
    """
    try:
        engine = JinaEmbeddingService.get_instance()
        vectors = await engine.embed_texts(
            request.texts,
            task=request.task,
            mode=request.mode,
            provider=request.provider,
        )
        profile = engine.resolve_embedding_profile(provider=request.provider, mode=request.mode)
        return {"embeddings": vectors, "embedding_profile": profile}
    except Exception as e:
        logger.error("embedding_failed", error=str(e), text_count=len(request.texts))
        raise ApiError(status_code=500, code="EMBEDDING_FAILED", message="Embedding failed")


@router.post("/ingest")
async def ingest_document(
    response: Response,
    file: UploadFile = File(...),
    metadata: str = Form(...),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    """
    Standard Ingestion (Curricular/Public).
    Supports PDF (full pipeline) and pre-processed MD/TXT (fast path).
    """
    try:
        # Detect file extension for strategy routing
        import json

        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext in ("md", "txt"):
            logger.info("pre_processed_fast_path", filename=filename, extension=ext)
            # Inject strategy override into metadata
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError:
                meta_dict = {}

            if "metadata" not in meta_dict or meta_dict["metadata"] is None:
                meta_dict["metadata"] = {}
            meta_dict["metadata"]["strategy_override"] = "PRE_PROCESSED"
            metadata = json.dumps(meta_dict)

        file_path, original_filename, parsed_metadata = await use_case.execute(file, metadata)
        enqueue_result = await use_case.process_background(
            file_path=file_path,
            original_filename=original_filename,
            metadata=parsed_metadata,
        )
        queue = enqueue_result["queue"]
        response.headers["X-Queue-Depth"] = str(int(queue.get("queue_depth") or 0))
        response.headers["X-Queue-ETA-Seconds"] = str(int(queue.get("estimated_wait_seconds") or 0))
        max_pending = queue.get("max_pending")
        if max_pending is not None:
            response.headers["X-Queue-Max-Pending"] = str(int(max_pending))
        return {
            "status": "accepted",
            "message": "Ingestion queued",
            "document_id": enqueue_result["document_id"],
            "queue": queue,
        }
    except ValueError as e:
        detail = str(e)
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(
                status_code=429,
                code="INGESTION_BACKPRESSURE",
                message="Ingestion queue is saturated",
                details=detail,
            )
        if "COLLECTION_SEALED" in str(e):
            raise ApiError(
                status_code=409,
                code="COLLECTION_SEALED",
                message="Collection is sealed",
                details=str(e),
            )
        raise ApiError(
            status_code=400,
            code="INVALID_INGESTION_REQUEST",
            message="Invalid ingestion request",
            details=str(e),
        )
    except Exception as e:
        logger.error("ingestion_failed", error=str(e))
        raise ApiError(status_code=500, code="INGESTION_FAILED", message="Ingestion failed")


@router.post("/institutional")
async def ingest_institutional_document(
    request: InstitutionalIngestionRequest,
    response: Response,
    use_case: InstitutionalIngestionUseCase = Depends(get_institutional_use_case),
):
    """
    Endpoint for Institutional Ingestion triggered via Webhook.
    """
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        result = await use_case.execute(
            tenant_id=tenant_id,
            file_path=request.file_path,
            document_id=request.document_id,
            metadata=request.metadata,
        )
        queue = result.get("queue")
        if isinstance(queue, dict):
            response.headers["X-Queue-Depth"] = str(int(queue.get("queue_depth") or 0))
            response.headers["X-Queue-ETA-Seconds"] = str(
                int(queue.get("estimated_wait_seconds") or 0)
            )
            max_pending = queue.get("max_pending")
            if max_pending is not None:
                response.headers["X-Queue-Max-Pending"] = str(int(max_pending))

        return result
    except ValueError as e:
        detail = str(e)
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(
                status_code=429,
                code="INGESTION_BACKPRESSURE",
                message="Ingestion queue is saturated",
                details=detail,
            )
        if "COLLECTION_SEALED" in str(e):
            raise ApiError(
                status_code=409,
                code="COLLECTION_SEALED",
                message="Collection is sealed",
                details=str(e),
            )
        raise ApiError(
            status_code=400,
            code="INVALID_INSTITUTIONAL_REQUEST",
            message="Invalid institutional ingestion request",
            details=str(e),
        )
    except Exception as e:
        logger.error("institutional_ingestion_failed", error=str(e), tenant_id=request.tenant_id)
        raise ApiError(
            status_code=500,
            code="INSTITUTIONAL_INGESTION_FAILED",
            message="Institutional ingestion failed",
        )


@router.get("/documents")
async def list_documents(
    limit: int = 20, use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case)
):
    """
    List registered source documents from Supabase.
    """
    try:
        require_tenant_from_context()
        docs = await use_case.get_documents(limit)
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
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        return await use_case.list_collections(tenant_id=tenant_ctx)
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


@router.get("/queue/status")
async def get_ingestion_queue_status(
    tenant_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        queue = await use_case.get_queue_status(tenant_id=tenant_ctx)
        return {
            "status": "ok",
            "tenant_id": tenant_ctx,
            "queue": queue,
        }
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        if detail == "INVALID_TENANT_ID":
            raise ApiError(
                status_code=400,
                code="INVALID_TENANT_ID",
                message="Invalid tenant id",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_QUEUE_REQUEST",
            message="Invalid queue status request",
            details=detail,
        )
    except Exception as e:
        logger.error("get_queue_status_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(
            status_code=500, code="QUEUE_STATUS_FAILED", message="Failed to get queue status"
        )


@router.post("/collections/cleanup")
async def cleanup_collection(
    request: CleanupCollectionRequest,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        return await use_case.cleanup_collection(
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


@router.post("/retry/{doc_id}")
async def retry_ingestion_endpoint(
    doc_id: str, use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case)
):
    """
    Retry ingestion for an existing document ID.
    """
    try:
        require_tenant_from_context()
        await use_case.retry_ingestion(doc_id)

        # No need to add background task. The UPDATE triggers the worker via Postgres Changes.

        return {"status": "accepted", "message": f"Retry for {doc_id} queued"}
    except FileNotFoundError as e:
        raise ApiError(
            status_code=404, code="DOCUMENT_NOT_FOUND", message="Document not found", details=str(e)
        )
    except ValueError as e:
        raise ApiError(
            status_code=400,
            code="INVALID_RETRY_REQUEST",
            message="Invalid retry request",
            details=str(e),
        )
    except Exception as e:
        logger.error("retry_ingestion_failed", doc_id=doc_id, error=str(e))
        raise ApiError(status_code=500, code="RETRY_FAILED", message="Retry failed")


@router.post("/enrich/{doc_id}")
async def replay_enrichment_endpoint(
    doc_id: str,
    request: ReplayEnrichmentRequest,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    """Enqueue deferred enrichment (visual/graph/raptor) without re-ingesting the file."""
    try:
        tenant_id = require_tenant_from_context()
        result = await use_case.enqueue_deferred_enrichment(
            doc_id=doc_id,
            tenant_id=tenant_id,
            include_visual=bool(request.include_visual),
            include_graph=bool(request.include_graph),
            include_raptor=bool(request.include_raptor),
        )
        return {"status": "accepted", **result}
    except FileNotFoundError as e:
        raise ApiError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found",
            details=str(e),
        )
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400,
                code="TENANT_MISMATCH",
                message="Tenant mismatch",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_ENRICHMENT_REPLAY_REQUEST",
            message="Invalid enrichment replay request",
            details=detail,
        )
    except Exception as e:
        logger.error("replay_enrichment_failed", doc_id=doc_id, error=str(e))
        raise ApiError(
            status_code=500,
            code="REPLAY_ENRICHMENT_FAILED",
            message="Replay enrichment failed",
        )


@router.get("/jobs/{job_id}")
async def get_job_status_endpoint(
    job_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_id = require_tenant_from_context()
        row = await use_case.get_job_status(tenant_id=tenant_id, job_id=job_id)
        return {
            "id": row.get("id"),
            "job_type": row.get("job_type"),
            "status": row.get("status"),
            "error_message": row.get("error_message"),
            "result": row.get("result") if isinstance(row.get("result"), dict) else {},
            "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
    except ValueError as e:
        detail = str(e)
        if detail == "JOB_NOT_FOUND":
            raise ApiError(
                status_code=404,
                code="JOB_NOT_FOUND",
                message="Job not found",
                details=detail,
            )
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400,
                code="TENANT_MISMATCH",
                message="Tenant mismatch",
                details=detail,
            )
        raise ApiError(
            status_code=400,
            code="INVALID_JOB_STATUS_REQUEST",
            message="Invalid job status request",
            details=detail,
        )
    except Exception as e:
        logger.error("job_status_failed", job_id=job_id, error=str(e))
        raise ApiError(
            status_code=500,
            code="JOB_STATUS_FAILED",
            message="Failed to read job status",
        )


@router.post("/batches")
async def create_ingestion_batch(
    request: CreateBatchRequest,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        result = await use_case.create_batch(
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
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        require_tenant_from_context()
        result = await use_case.add_file_to_batch(batch_id=batch_id, file=file, metadata=metadata)
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
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        require_tenant_from_context()
        result = await use_case.seal_batch(batch_id=batch_id)
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


@router.get("/batches/{batch_id}/status")
async def get_ingestion_batch_status(
    batch_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        require_tenant_from_context()
        return await use_case.get_batch_status(batch_id=batch_id)
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
            code="INVALID_BATCH_STATUS_REQUEST",
            message="Invalid batch status request",
            details=detail,
        )
    except Exception as e:
        logger.error("get_batch_status_failed", batch_id=batch_id, error=str(e))
        raise ApiError(
            status_code=500, code="GET_BATCH_STATUS_FAILED", message="Get batch status failed"
        )


@router.get("/batches/{batch_id}/progress")
async def get_ingestion_batch_progress(
    batch_id: str,
    tenant_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        _ = tenant_ctx
        return await use_case.get_batch_progress(batch_id=batch_id)
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
            code="INVALID_BATCH_PROGRESS_REQUEST",
            message="Invalid batch progress request",
            details=detail,
        )
    except Exception as e:
        logger.error("get_batch_progress_failed", batch_id=batch_id, error=str(e))
        raise ApiError(
            status_code=500, code="GET_BATCH_PROGRESS_FAILED", message="Get batch progress failed"
        )


@router.get("/batches/{batch_id}/events")
async def get_ingestion_batch_events(
    batch_id: str,
    tenant_id: str,
    cursor: Optional[str] = None,
    limit: int = 100,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        _ = tenant_ctx
        return await use_case.get_batch_events(batch_id=batch_id, cursor=cursor, limit=limit)
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
            code="INVALID_BATCH_EVENTS_REQUEST",
            message="Invalid batch events request",
            details=detail,
        )
    except Exception as e:
        logger.error("get_batch_events_failed", batch_id=batch_id, error=str(e))
        raise ApiError(
            status_code=500, code="GET_BATCH_EVENTS_FAILED", message="Get batch events failed"
        )


@router.get("/batches/active")
async def list_active_ingestion_batches(
    tenant_id: str,
    limit: int = 10,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        return await use_case.list_active_batches(tenant_id=tenant_ctx, limit=limit)
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "TENANT_MISMATCH" in detail:
            raise ApiError(
                status_code=400, code="TENANT_MISMATCH", message="Tenant mismatch", details=detail
            )
        raise ApiError(
            status_code=400,
            code="INVALID_ACTIVE_BATCHES_REQUEST",
            message="Invalid active batches request",
            details=detail,
        )
    except Exception as e:
        logger.error("list_active_batches_failed", tenant_id=tenant_id, error=str(e))
        raise ApiError(
            status_code=500, code="LIST_ACTIVE_BATCHES_FAILED", message="List active batches failed"
        )


@router.get("/batches/{batch_id}/stream")
async def stream_ingestion_batch(
    batch_id: str,
    tenant_id: str,
    cursor: Optional[str] = None,
    interval_ms: int = 1500,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
    _ = tenant_ctx
    safe_interval_ms = max(500, min(int(interval_ms or 1500), 15000))
    session_timeout_seconds = 1800

    async def _event_stream():
        current_cursor = cursor
        started_at = time.monotonic()
        last_heartbeat = 0.0
        while True:
            progress = await use_case.get_batch_progress(batch_id=batch_id)
            snapshot_payload = {
                "type": "snapshot",
                "batch_id": batch_id,
                "cursor": progress.get("observability", {}).get("cursor"),
                "progress": progress,
            }
            yield f"event: snapshot\ndata: {json.dumps(snapshot_payload, ensure_ascii=True)}\n\n"

            delta = await use_case.get_batch_events(
                batch_id=batch_id, cursor=current_cursor, limit=100
            )
            items = delta.get("items") if isinstance(delta.get("items"), list) else []
            if items:
                current_cursor = str(delta.get("next_cursor") or current_cursor or "")
                delta_payload = {
                    "type": "delta",
                    "batch_id": batch_id,
                    "cursor": current_cursor,
                    "events": items,
                    "has_more": bool(delta.get("has_more", False)),
                }
                yield f"event: delta\ndata: {json.dumps(delta_payload, ensure_ascii=True)}\n\n"

            batch = progress.get("batch") if isinstance(progress.get("batch"), dict) else {}
            status = str(batch.get("status") or "").lower()
            if status in {"completed", "partial", "failed"}:
                terminal_payload = {
                    "type": "terminal",
                    "batch_id": batch_id,
                    "status": status,
                    "cursor": current_cursor,
                }
                yield f"event: terminal\ndata: {json.dumps(terminal_payload, ensure_ascii=True)}\n\n"
                return

            now = time.monotonic()
            if now - last_heartbeat >= 15.0:
                heartbeat_payload = {
                    "type": "heartbeat",
                    "batch_id": batch_id,
                    "at": int(time.time()),
                }
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat_payload, ensure_ascii=True)}\n\n"
                last_heartbeat = now

            if now - started_at > session_timeout_seconds:
                timeout_payload = {
                    "type": "terminal",
                    "batch_id": batch_id,
                    "status": "timeout",
                    "cursor": current_cursor,
                }
                yield f"event: terminal\ndata: {json.dumps(timeout_payload, ensure_ascii=True)}\n\n"
                return

            await asyncio.sleep(safe_interval_ms / 1000.0)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
