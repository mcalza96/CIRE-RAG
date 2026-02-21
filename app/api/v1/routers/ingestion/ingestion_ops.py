import structlog
import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, File, UploadFile, Form, Depends, Response
from pydantic import BaseModel

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match, require_tenant_from_context
from app.workflows.ingestion.trigger import IngestionTrigger
from app.api.dependencies import get_container
from app.services.database.taxonomy_manager import TaxonomyManager

logger = structlog.get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_service_auth)])

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


class ReplayEnrichmentRequest(BaseModel):
    include_visual: bool = True
    include_graph: bool = True
    include_raptor: bool = True


# --- DEPENDENCIES ---

def get_trigger(container=Depends(get_container)):
    return IngestionTrigger(
        repo=container.source_repository,
        taxonomy_manager=TaxonomyManager()
    )


# --- ENDPOINTS ---

@router.post("/embed")
async def embed_texts(request: EmbedRequest, container=Depends(get_container)):
    """
    Fast Inference Endpoint for text embedding.
    """
    try:
        engine = container.embedding_service
        vectors = await engine.embed_texts(
            request.texts,
            task=request.task,
            mode=request.mode,
            provider=request.provider,
        )
        profile = engine.resolve_embedding_profile(provider=request.provider, mode=request.mode)
        return {"embeddings": vectors, "embedding_profile": profile}
    except Exception as e:
        logger.error("embedding_failed", error=str(e), text_count=len(request.texts), exc_info=True)
        raise ApiError(status_code=500, code="EMBEDDING_FAILED", message="Embedding failed")


@router.post("/ingest")
async def ingest_document(
    response: Response,
    file: UploadFile = File(...),
    metadata: str = Form(...),
    trigger: IngestionTrigger = Depends(get_trigger),
):
    """
    Standard Ingestion (Curricular/Public).
    Supports PDF (full pipeline) and pre-processed MD/TXT (fast path).
    """
    try:
        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext in ("md", "txt"):
            logger.info("pre_processed_fast_path", filename=filename, extension=ext)
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError:
                meta_dict = {}

            if "metadata" not in meta_dict or meta_dict["metadata"] is None:
                meta_dict["metadata"] = {}
            meta_dict["metadata"]["strategy_override"] = "PRE_PROCESSED"
            metadata = json.dumps(meta_dict)

        file_path, original_filename, parsed_metadata = await trigger.prepare_manual_upload(file, metadata)
        enqueue_result = await trigger.trigger_manual_ingestion(
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
        logger.error("ingestion_failed", error=str(e), exc_info=True)
        raise ApiError(status_code=500, code="INGESTION_FAILED", message="Ingestion failed")


@router.post("/institutional")
async def ingest_institutional_document(
    request: InstitutionalIngestionRequest,
    response: Response,
    trigger: IngestionTrigger = Depends(get_trigger),
):
    """
    Endpoint for Institutional Ingestion triggered via Webhook.
    """
    try:
        tenant_id = enforce_tenant_match(request.tenant_id, "body.tenant_id")
        result = await trigger.trigger_institutional_ingestion(
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
        logger.error(
            "institutional_ingestion_failed",
            error=str(e),
            tenant_id=request.tenant_id,
            exc_info=True,
        )
        raise ApiError(
            status_code=500,
            code="INSTITUTIONAL_INGESTION_FAILED",
            message="Institutional ingestion failed",
        )


@router.post("/retry/{doc_id}")
async def retry_ingestion_endpoint(
    doc_id: str, 
    trigger: IngestionTrigger = Depends(get_trigger)
):
    """
    Retry ingestion for an existing document ID.
    """
    try:
        tenant_id = require_tenant_from_context()
        # IngestionTrigger doesn't have retry_ingestion, it was in BatchOrchestrator in the previous version
        # but the user said "ingestion_ops" is for "motores". 
        # Actually, retry_ingestion in ingestion.py was using orchestrator.retry_ingestion.
        # Let's import BatchOrchestrator here too if needed, or use a dependency.
        
        from app.services.ingestion.batch_orchestrator import BatchOrchestrator
        from app.infrastructure.container import CognitiveContainer
        
        orchestrator = BatchOrchestrator(
            taxonomy_manager=TaxonomyManager(),
            source_repo=CognitiveContainer().source_repository
        )
        
        await orchestrator.retry_ingestion(doc_id)
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
    container=Depends(get_container),
):
    """Enqueue deferred enrichment (visual/graph/raptor) without re-ingesting the file."""
    try:
        tenant_id = require_tenant_from_context()
        doc = await container.source_repository.get_by_id(doc_id)
        if not doc:
            raise FileNotFoundError(f"Document {doc_id} not found.")

        doc_tenant = str(doc.get("institution_id") or "").strip()
        if doc_tenant and doc_tenant != tenant_id:
            raise ValueError("TENANT_MISMATCH")

        collection_id = doc.get("collection_id")
        payload = {
            "source_document_id": str(doc_id),
            "collection_id": str(collection_id) if collection_id else None,
            "include_visual": bool(request.include_visual),
            "include_graph": bool(request.include_graph),
            "include_raptor": bool(request.include_raptor),
        }

        from app.infrastructure.supabase.client import get_async_supabase_client
        client = await get_async_supabase_client()
        
        existing = await (
            client.table("job_queue")
            .select("id")
            .eq("job_type", "enrich_document")
            .in_("status", ["pending", "processing"])
            .contains("payload", {"source_document_id": str(doc_id)})
            .limit(1)
            .execute()
        )
        
        if isinstance(existing.data, list) and existing.data:
            return {"status": "accepted", "already_queued": True, "job_id": existing.data[0].get("id")}

        inserted = await (
            client.table("job_queue")
            .insert({"job_type": "enrich_document", "tenant_id": tenant_id, "payload": payload})
            .execute()
        )
        job_id = inserted.data[0].get("id") if inserted.data else None

        await container.source_repository.log_event(
            doc_id=str(doc_id),
            message=f"Enrichment replay queued (visual={request.include_visual})",
            status="INFO",
            tenant_id=tenant_id,
            metadata={"phase": "enrichment_replay", **payload},
        )
        return {"status": "accepted", "already_queued": False, "job_id": job_id}

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
