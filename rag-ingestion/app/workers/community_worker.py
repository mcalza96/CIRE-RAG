import asyncio
import structlog
from uuid import UUID

from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.knowledge.clustering_service import ClusteringService

logger = structlog.get_logger(__name__)

JOB_TYPE = "community_rebuild"
POLL_INTERVAL = 5


async def _append_ingestion_event(tenant_id: str, payload: dict, status: str) -> None:
    supabase = await get_async_supabase_client()
    try:
        doc_res = await supabase.table("source_documents").select("id").eq(
            "institution_id", tenant_id
        ).order("created_at", desc=True).limit(1).execute()
        rows = doc_res.data or []
        if not rows:
            return
        doc_id = rows[0].get("id")
        if not doc_id:
            return

        await supabase.table("ingestion_events").insert(
            {
                "source_document_id": doc_id,
                "tenant_id": tenant_id,
                "message": (
                    f"Community rebuild tenant={tenant_id}: ok={payload.get('ok', False)} "
                    f"detected={payload.get('communities_detected', 0)} "
                    f"persisted={payload.get('communities_persisted', 0)}"
                ),
                "status": status,
                "node_type": "SYSTEM",
                "metadata": {
                    "phase": "community_rebuild",
                    "result": payload,
                },
            }
        ).execute()
    except Exception as exc:
        logger.warning("community_worker_event_log_failed", tenant_id=tenant_id, error=str(exc))


async def process_job(job: dict) -> None:
    supabase = await get_async_supabase_client()
    job_id = job["id"]
    tenant_id = str(job["tenant_id"])

    try:
        service = ClusteringService()
        result = await service.rebuild_communities(tenant_id=UUID(tenant_id))
        payload = {"ok": True, "tenant_id": tenant_id, **result}

        await supabase.table("job_queue").update(
            {
                "status": "completed",
                "result": payload,
            }
        ).eq("id", job_id).execute()

        await _append_ingestion_event(tenant_id, payload, status="SUCCESS")
        logger.info("community_job_completed", job_id=job_id, tenant_id=tenant_id)
    except Exception as exc:
        payload = {
            "ok": False,
            "tenant_id": tenant_id,
            "reason": "execution_error",
            "error": str(exc),
        }
        await supabase.table("job_queue").update(
            {
                "status": "failed",
                "error_message": str(exc),
                "result": payload,
            }
        ).eq("id", job_id).execute()

        await _append_ingestion_event(tenant_id, payload, status="WARNING")
        logger.error("community_job_failed", job_id=job_id, tenant_id=tenant_id, error=str(exc))


async def worker_loop() -> None:
    logger.info("Starting Community Worker")
    supabase = await get_async_supabase_client()

    while True:
        try:
            res = await supabase.rpc("fetch_next_job", {"p_job_type": JOB_TYPE}).execute()
            jobs = res.data or []
            if jobs:
                await process_job(jobs[0])
            else:
                await asyncio.sleep(POLL_INTERVAL)
        except Exception as exc:
            logger.error("community_worker_loop_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(worker_loop())
