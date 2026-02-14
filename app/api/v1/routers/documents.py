from __future__ import annotations

import json
from typing import Any, Dict, Optional, cast

import structlog
from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile
from pydantic import BaseModel, Field

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.tenant_guard import require_tenant_from_context
from app.api.v1.routers.ingestion import get_ingestion_use_case
from app.core.idempotency_store import get_idempotency_store, reset_idempotency_store_for_tests
from app.application.use_cases.manual_ingestion_use_case import ManualIngestionUseCase
from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository
from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_service_auth)])


def _idempotency_cache_key(idempotency_key: str) -> str:
    return f"documents:create:{idempotency_key.strip()}"


async def _lookup_idempotent_response(idempotency_key: Optional[str]) -> Optional[Any]:
    if not idempotency_key:
        return None
    store = await get_idempotency_store(ttl_seconds=600)
    payload = await store.get(_idempotency_cache_key(idempotency_key))
    if not payload:
        return None
    return DocumentCreateResponse.model_validate(payload)


async def _store_idempotent_response(idempotency_key: Optional[str], response_payload: Any) -> None:
    if not idempotency_key:
        return
    store = await get_idempotency_store(ttl_seconds=600)
    payload = response_payload.model_dump() if hasattr(response_payload, "model_dump") else dict(response_payload)
    await store.set(_idempotency_cache_key(idempotency_key), payload)


def _reset_idempotency_cache_for_tests() -> None:
    import asyncio

    asyncio.run(reset_idempotency_store_for_tests())


class QueueSnapshot(BaseModel):
    queue_depth: int = Field(default=0, examples=[3])
    max_pending: Optional[int] = Field(default=None, examples=[500])
    estimated_wait_seconds: int = Field(default=0, examples=[90])


class DocumentCreateResponse(BaseModel):
    status: str = Field(examples=["accepted"])
    message: str = Field(examples=["Ingestion queued"])
    document_id: str = Field(examples=["8d82f365-2c3f-4892-a777-7d9e58cd59f4"])
    queue: QueueSnapshot


class DocumentListResponse(BaseModel):
    items: list[Dict[str, Any]]


class DocumentStatusResponse(BaseModel):
    document_id: str
    status: str
    error_message: Optional[str] = None
    updated_at: Optional[str] = None


class DocumentDeleteResponse(BaseModel):
    status: str = Field(examples=["deleted"])
    document_id: str
    purge_chunks: bool = Field(examples=[True])


@router.post(
    "",
    operation_id="createDocument",
    summary="Create document ingestion",
    description="Uploads a document and queues ingestion for asynchronous processing.",
    response_model=DocumentCreateResponse,
    responses={
        200: {
            "description": "Document accepted and queued",
            "content": {
                "application/json": {
                    "example": {
                        "status": "accepted",
                        "message": "Ingestion queued",
                        "document_id": "8d82f365-2c3f-4892-a777-7d9e58cd59f4",
                        "queue": {"queue_depth": 3, "max_pending": 500, "estimated_wait_seconds": 90},
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        400: ERROR_RESPONSES[400],
        409: ERROR_RESPONSES[409],
        422: ERROR_RESPONSES[422],
        429: ERROR_RESPONSES[429],
        500: ERROR_RESPONSES[500],
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file", "metadata"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "metadata": {
                                "type": "string",
                                "description": "JSON metadata string for ingestion context",
                                "example": '{"institution_id":"tenant-demo","metadata":{"collection_key":"iso-9001"}}',
                            },
                        },
                    }
                }
            }
        }
    },
)
async def create_document(
    response: Response,
    file: UploadFile = File(...),
    metadata: str = Form(..., description="JSON metadata string for ingestion context"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
) -> DocumentCreateResponse:
    try:
        tenant_id = require_tenant_from_context()
        try:
            metadata_obj = json.loads(metadata)
        except Exception:
            metadata_obj = {}
        payload_tenant = metadata_obj.get("institution_id") or metadata_obj.get("tenant_id")
        if payload_tenant and str(payload_tenant).strip() != tenant_id:
            raise ApiError(
                status_code=400,
                code="TENANT_MISMATCH",
                message="Tenant mismatch",
                details="Tenant in metadata must match X-Tenant-ID header",
            )

        replayed = await _lookup_idempotent_response(idempotency_key)
        if replayed is not None:
            response.headers["X-Idempotency-Replayed"] = "true"
            return cast(DocumentCreateResponse, replayed)

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
        payload = DocumentCreateResponse(
            status="accepted",
            message="Ingestion queued",
            document_id=enqueue_result["document_id"],
            queue=QueueSnapshot(
                queue_depth=int(queue.get("queue_depth") or 0),
                max_pending=queue.get("max_pending"),
                estimated_wait_seconds=int(queue.get("estimated_wait_seconds") or 0),
            ),
        )
        await _store_idempotent_response(idempotency_key, payload)
        return payload
    except ApiError:
        raise
    except ValueError as e:
        detail = str(e)
        if "INGESTION_BACKPRESSURE" in detail:
            raise ApiError(status_code=429, code="INGESTION_BACKPRESSURE", message="Ingestion queue is saturated", details=detail)
        if "COLLECTION_SEALED" in detail:
            raise ApiError(status_code=409, code="COLLECTION_SEALED", message="Collection is sealed", details=detail)
        raise ApiError(status_code=400, code="INVALID_INGESTION_REQUEST", message="Invalid ingestion request", details=detail)
    except Exception as e:
        logger.error("documents_create_failed", error=str(e), filename=file.filename)
        raise ApiError(status_code=500, code="DOCUMENT_INGESTION_FAILED", message="Document ingestion failed")


@router.get(
    "",
    operation_id="listDocuments",
    summary="List documents",
    description="Lists source documents tracked by the ingestion subsystem.",
    response_model=DocumentListResponse,
    responses={
        200: {
            "description": "List source documents",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": "8d82f365-2c3f-4892-a777-7d9e58cd59f4",
                                "filename": "norma-iso-9001.pdf",
                                "status": "queued",
                            }
                        ]
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        500: ERROR_RESPONSES[500],
    },
)
async def list_documents(
    limit: int = 20,
    use_case: ManualIngestionUseCase = Depends(get_ingestion_use_case),
) -> DocumentListResponse:
    try:
        require_tenant_from_context()
        return DocumentListResponse(items=await use_case.get_documents(limit=limit))
    except ApiError:
        raise
    except Exception as e:
        logger.error("documents_list_failed", error=str(e), limit=limit)
        raise ApiError(status_code=500, code="DOCUMENT_LIST_FAILED", message="Failed to list documents")


@router.get(
    "/{document_id}/status",
    operation_id="getDocumentStatus",
    summary="Get document status",
    description="Returns ingestion status for a specific document id.",
    response_model=DocumentStatusResponse,
    responses={
        200: {
            "description": "Document ingestion status",
            "content": {
                "application/json": {
                    "example": {
                        "document_id": "8d82f365-2c3f-4892-a777-7d9e58cd59f4",
                        "status": "processing",
                        "error_message": None,
                        "updated_at": "2026-02-13T21:10:00Z",
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        404: ERROR_RESPONSES[404],
        500: ERROR_RESPONSES[500],
    },
)
async def get_document_status(document_id: str) -> DocumentStatusResponse:
    source_repo = SupabaseSourceRepository()
    try:
        require_tenant_from_context()
        doc = await source_repo.get_by_id(document_id)
        if not doc:
            raise ApiError(status_code=404, code="DOCUMENT_NOT_FOUND", message="Document not found", details={"document_id": document_id})

        return DocumentStatusResponse(
            document_id=str(doc.get("id") or document_id),
            status=str(doc.get("status") or "unknown"),
            error_message=doc.get("error_message"),
            updated_at=doc.get("updated_at"),
        )
    except ApiError:
        raise
    except Exception as e:
        logger.error("document_status_failed", document_id=document_id, error=str(e))
        raise ApiError(status_code=500, code="DOCUMENT_STATUS_FAILED", message="Failed to get document status")


@router.delete(
    "/{document_id}",
    operation_id="deleteDocument",
    summary="Delete document",
    description="Deletes a source document and optionally purges persisted chunks.",
    response_model=DocumentDeleteResponse,
    responses={
        200: {
            "description": "Document deleted",
            "content": {
                "application/json": {
                    "example": {
                        "status": "deleted",
                        "document_id": "8d82f365-2c3f-4892-a777-7d9e58cd59f4",
                        "purge_chunks": True,
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        404: ERROR_RESPONSES[404],
        500: ERROR_RESPONSES[500],
    },
)
async def delete_document(document_id: str, purge_chunks: Optional[bool] = True) -> DocumentDeleteResponse:
    source_repo = SupabaseSourceRepository()
    content_repo = SupabaseContentRepository()
    try:
        require_tenant_from_context()
        doc = await source_repo.get_by_id(document_id)
        if not doc:
            raise ApiError(status_code=404, code="DOCUMENT_NOT_FOUND", message="Document not found", details={"document_id": document_id})

        if purge_chunks:
            await content_repo.delete_chunks_by_source_id(document_id)
        await source_repo.delete_document(document_id)

        return DocumentDeleteResponse(
            status="deleted",
            document_id=document_id,
            purge_chunks=bool(purge_chunks),
        )
    except ApiError:
        raise
    except Exception as e:
        logger.error("document_delete_failed", document_id=document_id, error=str(e))
        raise ApiError(status_code=500, code="DOCUMENT_DELETE_FAILED", message="Failed to delete document")
