import structlog
import asyncio
import json
import time
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match, require_tenant_from_context
from app.services.ingestion.batch_orchestrator import BatchOrchestrator
from app.api.dependencies import get_container
from app.services.database.taxonomy_manager import TaxonomyManager

logger = structlog.get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_service_auth)])

# --- DEPENDENCIES ---

def get_batch_orchestrator(container=Depends(get_container)):
    return BatchOrchestrator(
        taxonomy_manager=TaxonomyManager(),
        source_repo=container.source_repository
    )


# --- ENDPOINTS ---

@router.get("/queue/status")
async def get_ingestion_queue_status(
    tenant_id: str,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        queue = await orchestrator.backpressure.get_pending_snapshot(tenant_id=tenant_ctx)
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


@router.get("/jobs/{job_id}")
async def get_job_status_endpoint(
    job_id: str,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_id = require_tenant_from_context()
        row = await orchestrator.query_service.get_job_status(tenant_id=tenant_id, job_id=job_id)
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


@router.get("/batches/{batch_id}/status")
async def get_ingestion_batch_status(
    batch_id: str,
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        require_tenant_from_context()
        return await orchestrator.get_batch_status(batch_id=batch_id)
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
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        _ = tenant_ctx
        status = await orchestrator.get_batch_status(batch_id=batch_id)
        return {
            "batch": status["batch"],
            "observability": status["observability"],
            "worker_progress": status["worker_progress"],
            "visual_accounting": status["visual_accounting"],
            "documents_count": len(status.get("documents") or []),
        }
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
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        _ = tenant_ctx
        payload = await orchestrator.query_service.get_batch_events(batch_id=str(batch_id), cursor=cursor, limit=limit)
        items = payload.get("items") or []
        for item in items:
            item["stage"] = orchestrator.observability.infer_worker_stage(item.get("message", ""))
        return payload
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
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
):
    try:
        tenant_ctx = enforce_tenant_match(tenant_id, "query.tenant_id")
        return await orchestrator.list_active_batches(tenant_id=tenant_ctx, limit=limit)
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
    orchestrator: BatchOrchestrator = Depends(get_batch_orchestrator),
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
            # Re-using BatchOrchestrator status logic
            progress = await orchestrator.get_batch_status(batch_id=batch_id)
            snapshot_payload = {
                "type": "snapshot",
                "batch_id": batch_id,
                "cursor": progress.get("observability", {}).get("cursor"),
                "progress": progress,
            }
            yield f"event: snapshot\ndata: {json.dumps(snapshot_payload, ensure_ascii=True)}\n\n"

            delta = await orchestrator.query_service.get_batch_events(
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
