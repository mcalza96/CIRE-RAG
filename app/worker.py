import asyncio
import logging
import time
from typing import Any, Dict, Optional

# Configure basic logging for worker visibility
logging.basicConfig(level=logging.INFO, format="%(asctime)s [WORKER] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Reduce third-party noise during ingestion/enrichment loops.
for noisy_logger in (
    "google_genai",
    "google.generativeai",
    "httpx",
    "httpcore",
):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase
from app.core.observability.ingestion_logging import compact_error, emit_event
from app.core.settings import settings
from app.domain.policies.ingestion_policy import IngestionPolicy
from app.infrastructure.adapters.supabase_metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.container import CognitiveContainer
from app.infrastructure.queue.supabase_job_store import SupabaseJobStore
from app.services.database.taxonomy_manager import TaxonomyManager
from app.workflows.ingestion.dispatcher import IngestionDispatcher
import app.workflows.ingestion.strategies  # Trigger strategy registration

# Import and re-export for backward compatibility
from app.schedules.community_scheduler import (
    CommunityScheduler,
    rebuild_community_graph_task as rebuild_community_graph_task,
)


class IngestionWorker:
    def __init__(self):
        self.is_running = True
        self._active_doc_ids = set()  # Memory Lock for concurrency control
        self._active_lock = asyncio.Lock()

        # Load concurrency settings
        self.worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 3)))
        self.worker_per_tenant_concurrency = max(
            1,
            int(getattr(settings, "WORKER_PER_TENANT_CONCURRENCY", 1)),
        )
        self.worker_poll_interval_seconds = max(
            1,
            int(getattr(settings, "WORKER_POLL_INTERVAL_SECONDS", 2)),
        )
        self.worker_source_lookup_max_requeues = max(
            0,
            int(getattr(settings, "WORKER_SOURCE_LOOKUP_MAX_REQUEUES", 3) or 3),
        )
        self.enrichment_worker_concurrency = max(
            1,
            int(getattr(settings, "ENRICHMENT_WORKER_CONCURRENCY", 2)),
        )

        self._semaphore = asyncio.Semaphore(self.worker_concurrency)
        self._tenant_semaphores: dict[str, asyncio.Semaphore] = {}
        self._tenant_semaphores_lock = asyncio.Lock()
        self._tenant_active_jobs: dict[str, int] = {}
        self._tenant_active_jobs_lock = asyncio.Lock()
        self._source_lookup_requeues_by_job: dict[str, int] = {}

        # Queue Alerts configuration
        self.worker_tenant_queue_sample_limit = max(
            1,
            int(getattr(settings, "WORKER_TENANT_QUEUE_SAMPLE_LIMIT", 1000)),
        )
        self.worker_tenant_queue_depth_alert = max(
            1,
            int(getattr(settings, "WORKER_TENANT_QUEUE_DEPTH_ALERT", 200)),
        )
        self.worker_tenant_queue_wait_alert_seconds = max(
            1,
            int(getattr(settings, "WORKER_TENANT_QUEUE_WAIT_ALERT_SECONDS", 300)),
        )

        # 1. Use Container for all dependencies
        container = CognitiveContainer.get_instance()
        self.container = container

        # 1.1 RAPTOR Dependencies
        from app.infrastructure.repositories.supabase_raptor_repository import (
            SupabaseRaptorRepository,
        )
        from app.services.knowledge.raptor_processor import RaptorProcessor

        self.raptor_repo = SupabaseRaptorRepository()
        self.raptor_processor = RaptorProcessor(repository=self.raptor_repo)

        # 2. Domain & Application Logic
        self.policy = IngestionPolicy()
        self.dispatcher = IngestionDispatcher()

        # 3. Use Case orchestration
        self.process_use_case = ProcessDocumentWorkerUseCase(
            repository=container.source_repository,
            content_repo=container.content_repository,
            storage_service=container.storage_service,
            dispatcher=self.dispatcher,
            taxonomy_manager=TaxonomyManager(),
            metadata_adapter=SupabaseMetadataAdapter(),
            policy=self.policy,
            raptor_processor=self.raptor_processor,
            raptor_repo=self.raptor_repo,
            # NEW: Pass specialized services from container
            download_service=container.download_service,
            state_manager=container.state_manager,
        )

        self.source_repository = container.source_repository

        # 4. Infrastructure Components (New)
        self.job_store = SupabaseJobStore()
        self.community_scheduler = CommunityScheduler(source_repository=self.source_repository)

    async def _process_source_record(self, record: Dict[str, Any]) -> bool:
        doc_id = record.get("id")
        if not doc_id:
            logger.warning("ingestion_job_without_document_id")
            return False
        tenant_key = self._resolve_tenant_key(record)

        try:
            async with self._active_lock:
                if doc_id in self._active_doc_ids:
                    logger.debug(
                        "[Worker] Document %s is already being processed. Skipping redundant event.",
                        doc_id,
                    )
                    return False
                self._active_doc_ids.add(doc_id)

            try:
                async with self._semaphore:
                    tenant_semaphore = await self._get_tenant_semaphore(tenant_key)
                    async with tenant_semaphore:
                        await self._increment_active_jobs(tenant_key)
                        await self._emit_tenant_runtime_metrics(
                            tenant_key=tenant_key, trigger="start", doc_id=str(doc_id)
                        )
                        await self.process_use_case.execute(record)
                        await self.job_store.update_batch_progress(record=record, success=True)
            except Exception:
                await self.job_store.update_batch_progress(record=record, success=False)
                raise
            finally:
                await self._decrement_active_jobs(tenant_key)
                await self._emit_tenant_runtime_metrics(
                    tenant_key=tenant_key, trigger="finish", doc_id=str(doc_id)
                )
                async with self._active_lock:
                    if doc_id in self._active_doc_ids:
                        self._active_doc_ids.remove(doc_id)

            return True
        except Exception as e:
            logger.error(f"Error en Worker para documento {doc_id}: {e}", exc_info=True)
            return False

    async def _run_single_ingestion_job(self, poller_id: int) -> bool:
        await self.job_store.maybe_requeue_stale_processing_jobs(
            job_type="ingest_document", poller_id=poller_id
        )
        job = await self.job_store.fetch_next_job(job_type="ingest_document")
        if not job:
            return False

        job_id = str(job.get("id") or "")
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        source_document_id = str(payload.get("source_document_id") or "")
        if not source_document_id:
            logger.error("ingestion_job_invalid_payload job_id=%s payload=%s", job_id, payload)
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message="Missing source_document_id in payload",
                result={"ok": False, "reason": "invalid_payload", "payload": payload},
            )
            return True

        logger.info(
            "poller=%s processing_ingestion_job job_id=%s source_document_id=%s",
            poller_id,
            job_id,
            source_document_id,
        )
        stop_signal, heartbeat_task = await self.job_store.with_job_heartbeat(job_id=job_id)
        t0 = time.perf_counter()
        try:
            try:
                record = await self.job_store.load_source_document(
                    source_document_id=source_document_id
                )
                self._source_lookup_requeues_by_job.pop(job_id, None)
            except Exception as exc:
                is_transient = self.job_store.is_transient_supabase_transport_error(exc) or (
                    "supabase_empty_response" in str(exc)
                )
                if not is_transient:
                    raise
                current = int(self._source_lookup_requeues_by_job.get(job_id, 0)) + 1
                self._source_lookup_requeues_by_job[job_id] = current
                if current > self.worker_source_lookup_max_requeues:
                    await self.job_store.mark_job_final(
                        job_id=job_id,
                        status="failed",
                        error_message=(
                            f"source_document_lookup_transient_exhausted:{compact_error(exc)}"
                        ),
                        result={
                            "ok": False,
                            "reason": "source_document_lookup_transient_exhausted",
                            "source_document_id": source_document_id,
                            "attempts": current,
                        },
                    )
                    self._source_lookup_requeues_by_job.pop(job_id, None)
                    emit_event(
                        logger,
                        "ingestion_job_failed_transient_source_lookup_exhausted",
                        level="error",
                        poller_id=poller_id,
                        job_id=job_id,
                        source_document_id=source_document_id,
                        attempts=current,
                        max_requeues=self.worker_source_lookup_max_requeues,
                        error=compact_error(exc),
                    )
                    return True
                await self.job_store.requeue_job_for_retry(
                    job_id=job_id,
                    error_message=(
                        "source_document_lookup_transient:"
                        f"attempt={current}/{self.worker_source_lookup_max_requeues}:"
                        f"{compact_error(exc)}"
                    ),
                )
                emit_event(
                    logger,
                    "ingestion_job_requeued_transient_source_lookup",
                    level="warning",
                    poller_id=poller_id,
                    job_id=job_id,
                    source_document_id=source_document_id,
                    attempts=current,
                    max_requeues=self.worker_source_lookup_max_requeues,
                    error=compact_error(exc),
                )
                return True
            if not record:
                await self.job_store.mark_job_final(
                    job_id=job_id,
                    status="failed",
                    error_message=f"source_document_not_found:{source_document_id}",
                    result={
                        "ok": False,
                        "reason": "source_document_not_found",
                        "source_document_id": source_document_id,
                    },
                )
                return True

            processed = await self._process_source_record(record)
            status = str(record.get("status") or "").lower()
            if processed:
                duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                emit_event(
                    logger,
                    "ingestion_job_summary",
                    poller_id=poller_id,
                    job_id=job_id,
                    source_document_id=source_document_id,
                    ok=True,
                    final_status=status,
                    duration_ms=duration_ms,
                )
                await self.job_store.mark_job_final(
                    job_id=job_id,
                    status="completed",
                    result={
                        "ok": True,
                        "source_document_id": source_document_id,
                        "final_status": status,
                    },
                )
                return True

            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message=f"ingestion_processing_failed:{source_document_id}",
                result={
                    "ok": False,
                    "reason": "processing_failed",
                    "source_document_id": source_document_id,
                    "final_status": status,
                },
            )
            duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            emit_event(
                logger,
                "ingestion_job_summary",
                level="warning",
                poller_id=poller_id,
                job_id=job_id,
                source_document_id=source_document_id,
                ok=False,
                final_status=status,
                duration_ms=duration_ms,
                error_code="PROCESSING_FAILED",
            )
            return True
        finally:
            await self.job_store.stop_job_heartbeat(stop_signal=stop_signal, task=heartbeat_task)

    async def _run_single_enrichment_job(self, poller_id: int) -> bool:
        await self.job_store.maybe_requeue_stale_processing_jobs(
            job_type="enrich_document", poller_id=poller_id
        )
        job = await self.job_store.fetch_next_job(job_type="enrich_document")
        if not job:
            return False

        job_id = str(job.get("id") or "")
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        source_document_id = str(payload.get("source_document_id") or "")
        if not source_document_id:
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message="Missing source_document_id in payload",
                result={"ok": False, "reason": "invalid_payload", "payload": payload},
            )
            return True

        try:
            record = await self.job_store.load_source_document(
                source_document_id=source_document_id
            )
            self._source_lookup_requeues_by_job.pop(job_id, None)
        except Exception as exc:
            is_transient = self.job_store.is_transient_supabase_transport_error(exc) or (
                "supabase_empty_response" in str(exc)
            )
            if not is_transient:
                raise
            current = int(self._source_lookup_requeues_by_job.get(job_id, 0)) + 1
            self._source_lookup_requeues_by_job[job_id] = current
            if current > self.worker_source_lookup_max_requeues:
                await self.job_store.mark_job_final(
                    job_id=job_id,
                    status="failed",
                    error_message=(
                        f"source_document_lookup_transient_exhausted:{compact_error(exc)}"
                    ),
                    result={
                        "ok": False,
                        "reason": "source_document_lookup_transient_exhausted",
                        "source_document_id": source_document_id,
                        "attempts": current,
                    },
                )
                self._source_lookup_requeues_by_job.pop(job_id, None)
                emit_event(
                    logger,
                    "enrichment_job_failed_transient_source_lookup_exhausted",
                    level="error",
                    poller_id=poller_id,
                    job_id=job_id,
                    source_document_id=source_document_id,
                    attempts=current,
                    max_requeues=self.worker_source_lookup_max_requeues,
                    error=compact_error(exc),
                )
                return True
            await self.job_store.requeue_job_for_retry(
                job_id=job_id,
                error_message=(
                    "source_document_lookup_transient:"
                    f"attempt={current}/{self.worker_source_lookup_max_requeues}:"
                    f"{compact_error(exc)}"
                ),
            )
            emit_event(
                logger,
                "enrichment_job_requeued_transient_source_lookup",
                level="warning",
                poller_id=poller_id,
                job_id=job_id,
                source_document_id=source_document_id,
                attempts=current,
                max_requeues=self.worker_source_lookup_max_requeues,
                error=compact_error(exc),
            )
            return True
        if not record:
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message=f"source_document_not_found:{source_document_id}",
                result={
                    "ok": False,
                    "reason": "source_document_not_found",
                    "source_document_id": source_document_id,
                },
            )
            return True

        tenant_id = record.get("institution_id")
        collection_id = payload.get("collection_id") or record.get("collection_id")
        include_visual = bool(payload.get("include_visual", False))
        include_graph = bool(payload.get("include_graph", True))
        include_raptor = bool(payload.get("include_raptor", True))
        logger.info(
            "poller=%s processing_enrichment_job job_id=%s source_document_id=%s visual=%s graph=%s raptor=%s",
            poller_id,
            job_id,
            source_document_id,
            include_visual,
            include_graph,
            include_raptor,
        )
        stop_signal, heartbeat_task = await self.job_store.with_job_heartbeat(job_id=job_id)
        t0 = time.perf_counter()
        try:
            result = await self.process_use_case.post_ingestion_service.run_deferred_enrichment(
                doc_id=source_document_id,
                tenant_id=str(tenant_id) if tenant_id else None,
                collection_id=str(collection_id) if collection_id else None,
                include_visual=include_visual,
                include_graph=include_graph,
                include_raptor=include_raptor,
            )
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="completed",
                result=result,
            )
            emit_event(
                logger,
                "enrichment_job_summary",
                poller_id=poller_id,
                job_id=job_id,
                source_document_id=source_document_id,
                ok=True,
                duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                include_visual=include_visual,
                include_graph=include_graph,
                include_raptor=include_raptor,
                chunks=result.get("chunks") if isinstance(result, dict) else None,
                visual_stitched=result.get("visual_stitched") if isinstance(result, dict) else None,
            )
        except Exception as exc:
            await self.job_store.mark_job_final(
                job_id=job_id,
                status="failed",
                error_message=str(exc),
                result={
                    "ok": False,
                    "reason": "enrichment_failed",
                    "source_document_id": source_document_id,
                    "error": compact_error(exc),
                },
            )
            emit_event(
                logger,
                "enrichment_job_summary",
                level="error",
                poller_id=poller_id,
                job_id=job_id,
                source_document_id=source_document_id,
                ok=False,
                duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                include_visual=include_visual,
                include_graph=include_graph,
                include_raptor=include_raptor,
                error_code="ENRICHMENT_FAILED",
                error=compact_error(exc),
            )
        finally:
            await self.job_store.stop_job_heartbeat(stop_signal=stop_signal, task=heartbeat_task)
        return True

    async def _poller_loop(self, poller_id: int) -> None:
        logger.info("Ingestion poller started id=%s", poller_id)
        consecutive_errors = 0
        while self.is_running:
            try:
                processed = await self._run_single_ingestion_job(poller_id=poller_id)
                if processed:
                    consecutive_errors = 0
                if not processed:
                    await asyncio.sleep(self.worker_poll_interval_seconds)
            except Exception as exc:
                logger.error("poller=%s ingestion_loop_error=%s", poller_id, exc, exc_info=True)
                consecutive_errors += 1
                await self._maybe_cooldown_after_transport_errors(
                    poller_id=poller_id,
                    loop_name="ingestion",
                    consecutive_errors=consecutive_errors,
                    error=exc,
                )
                await asyncio.sleep(self.worker_poll_interval_seconds)

    async def _enrichment_poller_loop(self, poller_id: int) -> None:
        logger.info("Enrichment poller started id=%s", poller_id)
        consecutive_errors = 0
        while self.is_running:
            try:
                processed = await self._run_single_enrichment_job(poller_id=poller_id)
                if processed:
                    consecutive_errors = 0
                if not processed:
                    await asyncio.sleep(self.worker_poll_interval_seconds)
            except Exception as exc:
                logger.error("poller=%s enrichment_loop_error=%s", poller_id, exc, exc_info=True)
                consecutive_errors += 1
                await self._maybe_cooldown_after_transport_errors(
                    poller_id=poller_id,
                    loop_name="enrichment",
                    consecutive_errors=consecutive_errors,
                    error=exc,
                )
                await asyncio.sleep(self.worker_poll_interval_seconds)

    async def _maybe_cooldown_after_transport_errors(
        self,
        *,
        poller_id: int,
        loop_name: str,
        consecutive_errors: int,
        error: Exception,
    ) -> None:
        if not self.job_store.is_transient_supabase_transport_error(error):
            return
        threshold = max(
            1,
            int(getattr(settings, "WORKER_SUPABASE_ERROR_COOLDOWN_THRESHOLD", 4) or 4),
        )
        if consecutive_errors < threshold:
            return
        cooldown_seconds = max(
            0.5,
            float(getattr(settings, "WORKER_SUPABASE_ERROR_COOLDOWN_SECONDS", 6.0) or 6.0),
        )
        logger.warning(
            "worker_transport_circuit_cooldown poller=%s loop=%s consecutive_errors=%s threshold=%s cooldown_seconds=%s",
            poller_id,
            loop_name,
            consecutive_errors,
            threshold,
            cooldown_seconds,
        )
        await asyncio.sleep(cooldown_seconds)

    async def _get_tenant_semaphore(self, tenant_key: str) -> asyncio.Semaphore:
        async with self._tenant_semaphores_lock:
            semaphore = self._tenant_semaphores.get(tenant_key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self.worker_per_tenant_concurrency)
                self._tenant_semaphores[tenant_key] = semaphore
            return semaphore

    async def _increment_active_jobs(self, tenant_key: str) -> None:
        async with self._tenant_active_jobs_lock:
            current = int(self._tenant_active_jobs.get(tenant_key, 0))
            self._tenant_active_jobs[tenant_key] = current + 1

    async def _decrement_active_jobs(self, tenant_key: str) -> None:
        async with self._tenant_active_jobs_lock:
            current = int(self._tenant_active_jobs.get(tenant_key, 0))
            next_value = max(0, current - 1)
            if next_value == 0:
                self._tenant_active_jobs.pop(tenant_key, None)
            else:
                self._tenant_active_jobs[tenant_key] = next_value

    async def _get_active_jobs(self, tenant_key: str) -> int:
        async with self._tenant_active_jobs_lock:
            return int(self._tenant_active_jobs.get(tenant_key, 0))

    @staticmethod
    def _resolve_tenant_key(record: Dict[str, Any]) -> str:
        tenant_id = record.get("institution_id")
        if tenant_id:
            return str(tenant_id)
        metadata = record.get("metadata")
        if isinstance(metadata, dict) and metadata.get("institution_id"):
            return str(metadata.get("institution_id"))
        return "global"

    async def _emit_tenant_runtime_metrics(
        self, tenant_key: str, trigger: str, doc_id: str
    ) -> None:
        active_jobs = await self._get_active_jobs(tenant_key)
        metrics: Dict[str, Any] = {
            "tenant_id": tenant_key,
            "active_jobs": active_jobs,
            "trigger": trigger,
            "doc_id": doc_id,
        }

        if tenant_key != "global":
            try:
                queue_metrics = await self.job_store.get_tenant_queue_metrics(
                    tenant_key=tenant_key, sample_limit=self.worker_tenant_queue_sample_limit
                )
                metrics.update(queue_metrics)
                queue_depth = int(queue_metrics.get("queue_depth", 0))
                wait_seconds = int(queue_metrics.get("queue_wait_seconds", 0))

                logger.info("worker_tenant_runtime_metrics %s", metrics)

                if (
                    queue_depth >= self.worker_tenant_queue_depth_alert
                    or wait_seconds >= self.worker_tenant_queue_wait_alert_seconds
                    or active_jobs >= self.worker_per_tenant_concurrency
                ):
                    logger.warning(
                        "worker_tenant_saturation_alert tenant=%s active_jobs=%s limit=%s queue_depth=%s depth_alert=%s queue_wait_seconds=%s wait_alert=%s",
                        tenant_key,
                        active_jobs,
                        self.worker_per_tenant_concurrency,
                        queue_depth,
                        self.worker_tenant_queue_depth_alert,
                        wait_seconds,
                        self.worker_tenant_queue_wait_alert_seconds,
                    )
            except Exception as exc:
                logger.warning(
                    "worker_tenant_runtime_metrics_failed tenant=%s trigger=%s error=%s",
                    tenant_key,
                    trigger,
                    str(exc),
                )
        else:
            logger.info("worker_tenant_runtime_metrics %s", metrics)

    async def start(self):
        await self.job_store.get_client()
        logger.info("Starting RAG Ingestion Worker with pull model")
        logger.info(f"Worker concurrency configured: {self.worker_concurrency}")
        logger.info(f"Worker per-tenant concurrency: {self.worker_per_tenant_concurrency}")
        logger.info("Enrichment worker concurrency: %s", self.enrichment_worker_concurrency)
        logger.info("Worker poll interval: %ss", self.worker_poll_interval_seconds)
        logger.info(
            "Worker tenant alert settings: depth>=%s wait>=%ss sample_limit=%s",
            self.worker_tenant_queue_depth_alert,
            self.worker_tenant_queue_wait_alert_seconds,
            self.worker_tenant_queue_sample_limit,
        )

        async def scheduler_loop() -> None:
            while self.is_running:
                await self.community_scheduler.tick()
                await asyncio.sleep(1)

        scheduler_task = asyncio.create_task(scheduler_loop())
        poller_tasks = [
            asyncio.create_task(self._poller_loop(poller_id=i + 1))
            for i in range(self.worker_concurrency)
        ]
        enrichment_tasks = [
            asyncio.create_task(self._enrichment_poller_loop(poller_id=i + 1))
            for i in range(self.enrichment_worker_concurrency)
        ]

        try:
            await asyncio.gather(*poller_tasks, *enrichment_tasks, scheduler_task)
        finally:
            for task in poller_tasks:
                task.cancel()
            for task in enrichment_tasks:
                task.cancel()
            if scheduler_task:
                scheduler_task.cancel()


if __name__ == "__main__":
    worker = IngestionWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
