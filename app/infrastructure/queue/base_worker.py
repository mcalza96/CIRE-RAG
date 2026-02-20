import asyncio
import time
import structlog
from typing import Any, Dict, Optional, Callable, Coroutine
from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.infrastructure.observability.ingestion_logging import compact_error, emit_event

logger = structlog.get_logger(__name__)

class BaseWorkerProcessor:
    """
    Template for processing jobs from SupabaseJobStore.
    Handles polling, heartbeats, and error management.
    """
    def __init__(self, job_store: SupabaseJobStore, poller_id: int):
        self.job_store = job_store
        self.poller_id = poller_id

    async def run_job_loop(
        self,
        job_type: str,
        handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
        poll_interval: int = 2
    ):
        """
        Generic loop for polling and processing jobs.
        """
        consecutive_errors = 0
        while True:
            try:
                processed = await self._process_one_job(job_type, handler)
                if processed:
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(poll_interval)
            except Exception as exc:
                logger.error("worker_loop_error", job_type=job_type, poller_id=self.poller_id, error=str(exc), exc_info=True)
                consecutive_errors += 1
                # Cooldown logic could be here or handled by the caller
                await asyncio.sleep(poll_interval)

    async def _process_one_job(
        self,
        job_type: str,
        handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]]
    ) -> bool:
        await self.job_store.maybe_requeue_stale_processing_jobs(
            job_type=job_type, poller_id=self.poller_id
        )
        
        job = await self.job_store.fetch_next_job(job_type=job_type)
        if not job:
            return False

        job_id = str(job.get("id") or "")
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        
        logger.info("processing_job_start", job_type=job_type, job_id=job_id, poller_id=self.poller_id)
        
        stop_signal, heartbeat_task = await self.job_store.with_job_heartbeat(job_id=job_id)
        start_time = time.perf_counter()
        
        try:
            result = await handler(job)
            
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="completed",
                result=result
            )
            
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            logger.info("processing_job_completed", 
                        job_type=job_type, 
                        job_id=job_id, 
                        duration_ms=duration_ms)
            return True
            
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            error_msg = compact_error(exc)
            
            # Decide if we should retry or fail based on the exception or job state
            # For now, let's mark as failed unless it's a transient transport error?
            # Actually, the specific workers might want to handle retries themselves.
            
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message=error_msg,
                result={"ok": False, "error": error_msg}
            )
            
            logger.error("processing_job_failed", 
                         job_type=job_type, 
                         job_id=job_id, 
                         error=error_msg, 
                         duration_ms=duration_ms, 
                         exc_info=True)
            return True
            
        finally:
            await self.job_store.stop_job_heartbeat(stop_signal=stop_signal, task=heartbeat_task)
