import structlog
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, File, UploadFile, BackgroundTasks, HTTPException, Form, Depends, Header
from pydantic import BaseModel

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
        raise HTTPException(status_code=500, detail="Embedding failed")

@router.post("/ingest")
async def ingest_document(
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(
            use_case.process_background, 
            file_path=file_path, 
            original_filename=original_filename,
            metadata=parsed_metadata
        )
        return {"status": "accepted", "message": "Ingestion started"}
    except ValueError as e:
        if "COLLECTION_SEALED" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("ingestion_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Ingestion failed")

@router.post("/institutional")
async def ingest_institutional_document(
    request: InstitutionalIngestionRequest,
    background_tasks: BackgroundTasks,
    x_service_secret: Optional[str] = Header(None, alias="X-Service-Secret"),
    use_case: InstitutionalIngestionUseCase = Depends(get_institutional_use_case)
):
    """
    Endpoint for Institutional Ingestion triggered via Webhook.
    """
    if settings.RAG_SERVICE_SECRET and x_service_secret != settings.RAG_SERVICE_SECRET:
        logger.warning("unauthorized_access_attempt", service="institutional_ingestion")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Service Secret")
    
    try:
        result = await use_case.execute(
            tenant_id=request.tenant_id,
            file_path=request.file_path,
            document_id=request.document_id,
            metadata=request.metadata
        )

        # Backward compatibility: if a use case returns a background task, schedule it.
        background_task = result.pop("background_task", None)
        if background_task:
            func, *args = background_task
            if args:
                background_tasks.add_task(func, *args)
            else:
                background_tasks.add_task(func)
        
        return result
    except ValueError as e:
        if "COLLECTION_SEALED" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("institutional_ingestion_failed", error=str(e), tenant_id=request.tenant_id)
        raise HTTPException(status_code=500, detail="Institutional Ingestion failed")
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
        raise HTTPException(status_code=500, detail="Failed to list documents")


@router.get("/collections")
async def list_collections(
    tenant_id: str,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        return await use_case.list_collections(tenant_id=tenant_id)
    except Exception as e:
        logger.error("list_collections_failed", error=str(e), tenant_id=tenant_id)
        raise HTTPException(status_code=500, detail="Failed to list collections")


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
            raise HTTPException(status_code=404, detail=detail)
        if detail == "COLLECTION_SEALED":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        logger.error(
            "cleanup_collection_failed",
            tenant_id=request.tenant_id,
            collection_key=request.collection_key,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Cleanup collection failed")

@router.post("/retry/{doc_id}")
async def retry_ingestion_endpoint(
    doc_id: str,
    background_tasks: BackgroundTasks,
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
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("retry_ingestion_failed", doc_id=doc_id, error=str(e))
        raise HTTPException(status_code=500, detail="Retry failed")


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
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        logger.error("create_batch_failed", error=str(e), tenant_id=request.tenant_id)
        raise HTTPException(status_code=500, detail="Create batch failed")


@router.post("/batches/{batch_id}/files")
async def add_file_to_batch(
    batch_id: str,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
):
    try:
        result = await use_case.add_file_to_batch(batch_id=batch_id, file=file, metadata=metadata)
        return {"status": "accepted", **result}
    except ValueError as e:
        detail = str(e)
        if detail in {"BATCH_NOT_FOUND", "COLLECTION_NOT_FOUND"}:
            raise HTTPException(status_code=404, detail=detail)
        if detail == "COLLECTION_SEALED" or detail == "BATCH_FILE_LIMIT_EXCEEDED":
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        logger.error("add_file_to_batch_failed", batch_id=batch_id, error=str(e))
        detail = str(e)
        if "Bucket not found" in detail or "bucket" in detail.lower():
            raise HTTPException(status_code=500, detail=f"Storage bucket error: {detail}")
        raise HTTPException(status_code=500, detail="Add file to batch failed")


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
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        logger.error("seal_batch_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(status_code=500, detail="Seal batch failed")


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
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        logger.error("get_batch_status_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(status_code=500, detail="Get batch status failed")
