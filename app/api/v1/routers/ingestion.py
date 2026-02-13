import structlog
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, File, UploadFile, Form, Depends, Header, Response
from pydantic import BaseModel

from app.api.v1.errors import ApiError
from app.core.settings import settings
from app.application.use_cases.manual_ingestion_use_case import ManualIngestionUseCase
from app.application.use_cases.institutional_ingestion_use_case import InstitutionalIngestionUseCase
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.services.embedding_service import JinaEmbeddingService
from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

# --- SCHEMAS ---

class EmbedRequest(BaseModel):
    texts: List[str]
    task: Optional[str] = "retrieval.passage"

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
        vectors = await engine.embed_texts(request.texts, task=request.task)
        return {"embeddings": vectors}
    except Exception as e:
        logger.error("embedding_failed", error=str(e), text_count=len(request.texts))
        raise ApiError(status_code=500, code="EMBEDDING_FAILED", message="Embedding failed")

@router.post("/ingest")
async def ingest_document(
    response: Response,
    file: UploadFile = File(...),
    metadata: str = Form(...),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case)
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
            raise ApiError(status_code=429, code="INGESTION_BACKPRESSURE", message="Ingestion queue is saturated", details=detail)
        if "COLLECTION_SEALED" in str(e):
            raise ApiError(status_code=409, code="COLLECTION_SEALED", message="Collection is sealed", details=str(e))
        raise ApiError(status_code=400, code="INVALID_INGESTION_REQUEST", message="Invalid ingestion request", details=str(e))
    except Exception as e:
        logger.error("ingestion_failed", error=str(e))
        raise ApiError(status_code=500, code="INGESTION_FAILED", message="Ingestion failed")

@router.post("/institutional")
async def ingest_institutional_document(
    request: InstitutionalIngestionRequest,
    response: Response,
    x_service_secret: Optional[str] = Header(None, alias="X-Service-Secret"),
    use_case: InstitutionalIngestionUseCase = Depends(get_institutional_use_case)
):
    """
    Endpoint for Institutional Ingestion triggered via Webhook.
    """
    if settings.RAG_SERVICE_SECRET and x_service_secret != settings.RAG_SERVICE_SECRET:
        logger.warning("unauthorized_access_attempt", service="institutional_ingestion")
        raise ApiError(status_code=401, code="UNAUTHORIZED", message="Unauthorized: Invalid Service Secret")
    
    try:
        result = await use_case.execute(
            tenant_id=request.tenant_id,
            file_path=request.file_path,
            document_id=request.document_id,
            metadata=request.metadata
        )
        queue = result.get("queue")
        if isinstance(queue, dict):
            response.headers["X-Queue-Depth"] = str(int(queue.get("queue_depth") or 0))
            response.headers["X-Queue-ETA-Seconds"] = str(int(queue.get("estimated_wait_seconds") or 0))
            max_pending = queue.get("max_pending")
            if max_pending is not None:
                response.headers["X-Queue-Max-Pending"] = str(int(max_pending))

        return result
    except ValueError as e:
        detail = str(e)
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(status_code=429, code="INGESTION_BACKPRESSURE", message="Ingestion queue is saturated", details=detail)
        if "COLLECTION_SEALED" in str(e):
            raise ApiError(status_code=409, code="COLLECTION_SEALED", message="Collection is sealed", details=str(e))
        raise ApiError(status_code=400, code="INVALID_INSTITUTIONAL_REQUEST", message="Invalid institutional ingestion request", details=str(e))
    except Exception as e:
        logger.error("institutional_ingestion_failed", error=str(e), tenant_id=request.tenant_id)
        raise ApiError(status_code=500, code="INSTITUTIONAL_INGESTION_FAILED", message="Institutional ingestion failed")
@router.get("/documents")
async def list_documents(
    limit: int = 20,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case)
):
    """
    List registered source documents from Supabase.
    """
    try:
        docs = await use_case.get_documents(limit)
        return docs
    except Exception as e:
        logger.error("list_documents_failed", error=str(e))
        raise ApiError(status_code=500, code="DOCUMENT_LIST_FAILED", message="Failed to list documents")


@router.get("/collections")
async def list_collections(
    tenant_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        return await use_case.list_collections(tenant_id=tenant_id)
    except Exception as e:
        logger.error("list_collections_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(status_code=500, code="COLLECTION_LIST_FAILED", message="Failed to list collections")


@router.get("/queue/status")
async def get_ingestion_queue_status(
    tenant_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        queue = await use_case.get_queue_status(tenant_id=tenant_id)
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "queue": queue,
        }
    except ValueError as e:
        detail = str(e)
        if detail == "INVALID_TENANT_ID":
            raise ApiError(status_code=400, code="INVALID_TENANT_ID", message="Invalid tenant id", details=detail)
        raise ApiError(status_code=400, code="INVALID_QUEUE_REQUEST", message="Invalid queue status request", details=detail)
    except Exception as e:
        logger.error("get_queue_status_failed", error=str(e), tenant_id=tenant_id)
        raise ApiError(status_code=500, code="QUEUE_STATUS_FAILED", message="Failed to get queue status")


@router.post("/collections/cleanup")
async def cleanup_collection(
    request: CleanupCollectionRequest,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        return await use_case.cleanup_collection(
            tenant_id=request.tenant_id,
            collection_key=request.collection_key,
        )
    except ValueError as e:
        detail = str(e)
        if detail == "COLLECTION_NOT_FOUND":
            raise ApiError(status_code=404, code="COLLECTION_NOT_FOUND", message="Collection not found", details=detail)
        if detail == "COLLECTION_SEALED":
            raise ApiError(status_code=409, code="COLLECTION_SEALED", message="Collection is sealed", details=detail)
        raise ApiError(status_code=400, code="INVALID_COLLECTION_CLEANUP", message="Invalid cleanup request", details=detail)
    except Exception as e:
        logger.error(
            "cleanup_collection_failed",
            tenant_id=request.tenant_id,
            collection_key=request.collection_key,
            error=str(e),
        )
        raise ApiError(status_code=500, code="CLEANUP_COLLECTION_FAILED", message="Cleanup collection failed")

@router.post("/retry/{doc_id}")
async def retry_ingestion_endpoint(
    doc_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case)
):
    """
    Retry ingestion for an existing document ID.
    """
    try:
        await use_case.retry_ingestion(doc_id)
        
        # No need to add background task. The UPDATE triggers the worker via Postgres Changes.
        
        return {"status": "accepted", "message": f"Retry for {doc_id} queued"}
    except FileNotFoundError as e:
        raise ApiError(status_code=404, code="DOCUMENT_NOT_FOUND", message="Document not found", details=str(e))
    except ValueError as e:
        raise ApiError(status_code=400, code="INVALID_RETRY_REQUEST", message="Invalid retry request", details=str(e))
    except Exception as e:
        logger.error("retry_ingestion_failed", doc_id=doc_id, error=str(e))
        raise ApiError(status_code=500, code="RETRY_FAILED", message="Retry failed")


@router.post("/batches")
async def create_ingestion_batch(
    request: CreateBatchRequest,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        result = await use_case.create_batch(
            tenant_id=request.tenant_id,
            collection_key=request.collection_key,
            total_files=request.total_files,
            auto_seal=request.auto_seal,
            collection_name=request.collection_name,
            metadata=request.metadata,
        )
        return {"status": "accepted", **result}
    except ValueError as e:
        detail = str(e)
        if "COLLECTION_SEALED" in detail:
            raise ApiError(status_code=409, code="COLLECTION_SEALED", message="Collection is sealed", details=detail)
        raise ApiError(status_code=400, code="INVALID_BATCH_REQUEST", message="Invalid batch request", details=detail)
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
        result = await use_case.add_file_to_batch(batch_id=batch_id, file=file, metadata=metadata)
        queue = result.get("queue")
        if isinstance(queue, dict):
            response.headers["X-Queue-Depth"] = str(int(queue.get("queue_depth") or 0))
            response.headers["X-Queue-ETA-Seconds"] = str(int(queue.get("estimated_wait_seconds") or 0))
            max_pending = queue.get("max_pending")
            if max_pending is not None:
                response.headers["X-Queue-Max-Pending"] = str(int(max_pending))
        return {"status": "accepted", **result}
    except ValueError as e:
        detail = str(e)
        if detail in {"BATCH_NOT_FOUND", "COLLECTION_NOT_FOUND"}:
            raise ApiError(status_code=404, code=detail, message="Batch or collection not found", details=detail)
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(status_code=429, code="INGESTION_BACKPRESSURE", message="Ingestion queue is saturated", details=detail)
        if detail == "COLLECTION_SEALED" or detail == "BATCH_FILE_LIMIT_EXCEEDED":
            raise ApiError(status_code=409, code=detail, message="Batch cannot accept file", details=detail)
        raise ApiError(status_code=400, code="INVALID_BATCH_FILE_REQUEST", message="Invalid batch file request", details=detail)
    except Exception as e:
        logger.error("add_file_to_batch_failed", batch_id=batch_id, error=str(e))
        detail = str(e)
        if "Bucket not found" in detail or "bucket" in detail.lower():
            raise ApiError(status_code=500, code="STORAGE_BUCKET_ERROR", message="Storage bucket error", details=detail)
        raise ApiError(status_code=500, code="ADD_FILE_TO_BATCH_FAILED", message="Add file to batch failed")


@router.post("/batches/{batch_id}/seal")
async def seal_ingestion_batch(
    batch_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        result = await use_case.seal_batch(batch_id=batch_id)
        return {"status": "sealed", **result}
    except ValueError as e:
        detail = str(e)
        if detail == "BATCH_NOT_FOUND":
            raise ApiError(status_code=404, code="BATCH_NOT_FOUND", message="Batch not found", details=detail)
        raise ApiError(status_code=400, code="INVALID_SEAL_BATCH_REQUEST", message="Invalid seal batch request", details=detail)
    except Exception as e:
        logger.error("seal_batch_failed", batch_id=batch_id, error=str(e))
        raise ApiError(status_code=500, code="SEAL_BATCH_FAILED", message="Seal batch failed")


@router.get("/batches/{batch_id}/status")
async def get_ingestion_batch_status(
    batch_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        return await use_case.get_batch_status(batch_id=batch_id)
    except ValueError as e:
        detail = str(e)
        if detail == "BATCH_NOT_FOUND":
            raise ApiError(status_code=404, code="BATCH_NOT_FOUND", message="Batch not found", details=detail)
        raise ApiError(status_code=400, code="INVALID_BATCH_STATUS_REQUEST", message="Invalid batch status request", details=detail)
    except Exception as e:
        logger.error("get_batch_status_failed", batch_id=batch_id, error=str(e))
        raise ApiError(status_code=500, code="GET_BATCH_STATUS_FAILED", message="Get batch status failed")
