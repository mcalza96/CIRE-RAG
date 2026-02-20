import asyncio
import logging
import random
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.settings import settings
from app.infrastructure.supabase.client import (
    get_async_supabase_client,
    reset_async_supabase_client,
)

import structlog

logger = structlog.get_logger(__name__)


class SupabaseJobStore:
    """
    Handles all interactions with the Supabase job_queue and source_documents tables.
    Encapsulates polling logic, error handling, retries, and heartbeats.
    """

    def __init__(self):
        self._client = None
        self.worker_job_heartbeat_seconds = max(
            3.0,
            float(getattr(settings, "WORKER_JOB_HEARTBEAT_SECONDS", 20.0) or 20.0),
        )
        self.worker_requeue_stale_interval_seconds = max(
            5,
            int(getattr(settings, "WORKER_REQUEUE_STALE_INTERVAL_SECONDS", 15) or 15),
        )
        self.worker_requeue_stale_processing_seconds = max(
            30,
            int(getattr(settings, "WORKER_REQUEUE_STALE_PROCESSING_SECONDS", 120) or 120),
        )
        self._last_stale_requeue_at: dict[str, float] = {}

    async def get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client

    async def fetch_next_job(self, job_type: str) -> Optional[Dict[str, Any]]:
        max_retries = max(
            0,
            int(getattr(settings, "WORKER_SUPABASE_TRANSIENT_MAX_RETRIES", 3) or 3),
        )
        base_delay = max(
            0.05,
            float(getattr(settings, "WORKER_SUPABASE_TRANSIENT_BASE_DELAY_SECONDS", 0.4) or 0.4),
        )

        for attempt in range(max_retries + 1):
            try:
                client = await self.get_client()
                response = await client.rpc(
                    "fetch_next_job", {"p_job_type": str(job_type)}
                ).execute()
                if response is None:
                    raise RuntimeError("supabase_empty_response:fetch_next_job")
                jobs = response.data if isinstance(response.data, list) else []
                if not jobs:
                    return None
                first = jobs[0]
                return first if isinstance(first, dict) else None
            except Exception as exc:
                is_transient = self.is_transient_supabase_transport_error(exc) or (
                    "supabase_empty_response" in str(exc)
                )
                if not is_transient:
                    raise
                await self._reset_supabase_client_after_transport_error(
                    error=exc,
                    operation="fetch_next_job",
                    attempt=attempt + 1,
                    max_attempts=max_retries + 1,
                    job_type=job_type,
                )
                if attempt >= max_retries:
                    raise
                delay = min(base_delay * (2**attempt), 3.0) + random.uniform(0.0, 0.12)
                await asyncio.sleep(delay)

        return None

    async def load_source_document(self, source_document_id: str) -> Optional[Dict[str, Any]]:
        max_retries = max(
            0,
            int(getattr(settings, "WORKER_SUPABASE_TRANSIENT_MAX_RETRIES", 3) or 3),
        )
        base_delay = max(
            0.05,
            float(getattr(settings, "WORKER_SUPABASE_TRANSIENT_BASE_DELAY_SECONDS", 0.4) or 0.4),
        )

        for attempt in range(max_retries + 1):
            try:
                client = await self.get_client()
                response = (
                    await client.table("source_documents")
                    .select("*")
                    .eq("id", str(source_document_id))
                    .maybe_single()
                    .execute()
                )
                if response is None:
                    raise RuntimeError("supabase_empty_response:load_source_document")
                row = getattr(response, "data", None)
                return row if isinstance(row, dict) else None
            except Exception as exc:
                is_transient = self.is_transient_supabase_transport_error(exc) or (
                    "supabase_empty_response" in str(exc)
                )
                if not is_transient:
                    raise
                await self._reset_supabase_client_after_transport_error(
                    error=exc,
                    operation="load_source_document",
                    attempt=attempt + 1,
                    max_attempts=max_retries + 1,
                    job_type="source_document_lookup",
                )
                if attempt >= max_retries:
                    raise
                delay = min(base_delay * (2**attempt), 3.0) + random.uniform(0.0, 0.12)
                await asyncio.sleep(delay)

        return None

    async def mark_job_final(
        self,
        job_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        client = await self.get_client()
        payload: Dict[str, Any] = {
            "status": status,
            "result": result or {},
            "error_message": error_message,
        }
        await client.table("job_queue").update(payload).eq("id", str(job_id)).execute()

    async def requeue_job_for_retry(self, *, job_id: str, error_message: str) -> None:
        client = await self.get_client()
        await (
            client.table("job_queue")
            .update(
                {
                    "status": "pending",
                    "error_message": str(error_message or "transient_error"),
                }
            )
            .eq("id", str(job_id))
            .eq("status", "processing")
            .execute()
        )

    async def update_batch_progress(self, record: Dict[str, Any], success: bool) -> None:
        batch_id = record.get("batch_id")
        if not batch_id:
            return
        try:
            client = await self.get_client()
            await client.rpc(
                "update_batch_progress", {"p_batch_id": str(batch_id), "p_success": bool(success)}
            ).execute()
        except Exception as exc:
            logger.warning(
                f"batch_progress_update_failed batch_id={batch_id} success={success} error={exc}"
            )

    async def maybe_requeue_stale_processing_jobs(self, *, job_type: str, poller_id: int) -> None:
        now_monotonic = time.monotonic()
        last = float(self._last_stale_requeue_at.get(job_type, 0.0))
        if now_monotonic - last < float(self.worker_requeue_stale_interval_seconds):
            return
        self._last_stale_requeue_at[job_type] = now_monotonic

        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.worker_requeue_stale_processing_seconds
        )
        try:
            client = await self.get_client()
            response = (
                await client.table("job_queue")
                .update(
                    {
                        "status": "pending",
                        "error_message": (
                            f"stale_processing_requeued_by_worker(poller={poller_id},"
                            f"cutoff_seconds={self.worker_requeue_stale_processing_seconds})"
                        ),
                    }
                )
                .eq("job_type", str(job_type))
                .eq("status", "processing")
                .lt("updated_at", cutoff.isoformat())
                .execute()
            )
            rows = response.data if isinstance(response.data, list) else []
            if rows:
                logger.warning(
                    "worker_requeued_stale_processing_jobs poller=%s job_type=%s count=%s cutoff_seconds=%s",
                    poller_id,
                    job_type,
                    len(rows),
                    self.worker_requeue_stale_processing_seconds,
                )
        except Exception as exc:
            logger.warning(
                "worker_requeue_stale_processing_jobs_failed poller=%s job_type=%s error=%s",
                poller_id,
                job_type,
                exc,
            )

    async def with_job_heartbeat(self, job_id: str):
        stop_signal = asyncio.Event()
        task = asyncio.create_task(self._job_heartbeat_loop(job_id=job_id, stop_signal=stop_signal))
        return stop_signal, task

    async def stop_job_heartbeat(self, stop_signal: asyncio.Event, task: asyncio.Task) -> None:
        stop_signal.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _job_heartbeat_loop(self, job_id: str, stop_signal: asyncio.Event) -> None:
        while not stop_signal.is_set():
            await asyncio.sleep(self.worker_job_heartbeat_seconds)
            if stop_signal.is_set():
                break
            try:
                client = await self.get_client()
                await (
                    client.table("job_queue")
                    .update({"updated_at": datetime.now(timezone.utc).isoformat()})
                    .eq("id", str(job_id))
                    .eq("status", "processing")
                    .execute()
                )
            except Exception as exc:
                logger.warning("worker_job_heartbeat_failed job_id=%s error=%s", job_id, exc)

    async def _reset_supabase_client_after_transport_error(
        self,
        *,
        error: Exception,
        operation: str,
        attempt: int,
        max_attempts: int,
        job_type: str,
    ) -> None:
        logger.warning(
            "worker_supabase_transport_error operation=%s job_type=%s attempt=%s/%s error=%s",
            operation,
            job_type,
            attempt,
            max_attempts,
            str(error),
        )
        self._client = None
        reset_async_supabase_client()

    async def get_tenant_queue_metrics(self, tenant_key: str, sample_limit: int) -> Dict[str, Any]:
        """
        Consulta la profundidad de cola para un tenant específico.
        Retorna metrics como queue_depth y wait_seconds del trabajo más antiguo.
        """
        try:
            client = await self.get_client()
            queue_res = (
                await client.table("source_documents")
                .select("id,created_at")
                .eq("institution_id", tenant_key)
                .in_(
                    "status",
                    ["queued", "pending", "pending_ingestion", "processing", "processing_v2"],
                )
                .order("created_at", desc=False)
                .limit(sample_limit)
                .execute()
            )
            rows = queue_res.data or []
            queue_depth = len(rows)
            oldest_created_at = (
                rows[0].get("created_at") if rows and isinstance(rows[0], dict) else None
            )

            wait_seconds = 0
            if oldest_created_at:
                try:
                    text = str(oldest_created_at).strip()
                    if text.endswith("Z"):
                        text = text[:-1] + "+00:00"
                    oldest_dt = datetime.fromisoformat(text)
                    if oldest_dt.tzinfo is None:
                        oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
                    wait_seconds = max(
                        0, int((datetime.now(timezone.utc) - oldest_dt).total_seconds())
                    )
                except Exception:
                    pass

            return {
                "queue_depth": queue_depth,
                "queue_wait_seconds": wait_seconds,
                "queue_depth_sample_limit": sample_limit,
                "queue_depth_truncated": (queue_depth >= sample_limit),
            }
        except Exception as exc:
            logger.warning(
                "worker_get_tenant_queue_metrics_failed tenant=%s error=%s", tenant_key, exc
            )
            return {"queue_depth": 0, "queue_wait_seconds": 0, "error": str(exc)}

    @staticmethod
    def is_transient_supabase_transport_error(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        text = str(exc or "").lower()
        transient_markers = (
            "readerror",
            "connecterror",
            "remoteprotocolerror",
            "timeouterror",
            "pooltimeout",
            "temporarily unavailable",
            "connection reset",
            "broken pipe",
            "502",
            "503",
            "504",
            "bad gateway",
            "json could not be generated",
        )
        if any(marker in name for marker in transient_markers):
            return True
        if any(marker in text for marker in transient_markers):
            return True
        return False
