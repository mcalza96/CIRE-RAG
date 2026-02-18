import asyncio
import logging
from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

from app.infrastructure.container import CognitiveContainer
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.domain.policies.ingestion_policy import IngestionPolicy
from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase
from app.services.database.taxonomy_manager import TaxonomyManager
from app.infrastructure.adapters.supabase_metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.knowledge.clustering_service import ClusteringService
from app.core.settings import settings
import app.workflows.ingestion.strategies  # Trigger strategy registration

# Configure basic logging for worker visibility
logging.basicConfig(level=logging.INFO, format="%(asctime)s [WORKER] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def rebuild_community_graph_task(tenant_id: str) -> Dict[str, Any]:
    """
    Offline task: rebuild GraphRAG communities for one tenant.
    Safe to call from scheduler, worker hooks, or ad-hoc scripts.
    """
    try:
        tenant_uuid = UUID(str(tenant_id))
    except Exception as exc:
        logger.error("Invalid tenant_id for community rebuild: %s | error=%s", tenant_id, exc)
        return {"ok": False, "reason": "invalid_tenant_id", "tenant_id": tenant_id}

    try:
        service = ClusteringService()
        result = await service.rebuild_communities(tenant_uuid)

        if result.get("communities_detected", 0) == 0:
            logger.warning("Community rebuild skipped (empty graph) tenant=%s", tenant_uuid)

        payload = {"ok": True, "tenant_id": str(tenant_uuid), **result}
        logger.info("Community rebuild completed: %s", payload)
        return payload
    except Exception as exc:
        logger.error("Community rebuild failed tenant=%s error=%s", tenant_uuid, exc, exc_info=True)
        return {
            "ok": False,
            "tenant_id": str(tenant_uuid),
            "reason": "execution_error",
            "error": str(exc),
        }


class IngestionWorker:
    def __init__(self):
        self._client = None
        self.is_running = True
        self._active_doc_ids = set()  # Memory Lock for concurrency control
        self._active_lock = asyncio.Lock()
        self.worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 3)))
        self.worker_per_tenant_concurrency = max(
            1,
            int(getattr(settings, "WORKER_PER_TENANT_CONCURRENCY", 1)),
        )
        self.worker_poll_interval_seconds = max(
            1,
            int(getattr(settings, "WORKER_POLL_INTERVAL_SECONDS", 2)),
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
        self._last_community_rebuild_at: datetime | None = None
        self._community_rebuild_lock = asyncio.Lock()

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

        self.community_rebuild_enabled = settings.COMMUNITY_REBUILD_ENABLED
        self.community_rebuild_interval_seconds = settings.COMMUNITY_REBUILD_INTERVAL_SECONDS
        self.community_rebuild_tenants = [
            token.strip()
            for token in settings.COMMUNITY_REBUILD_TENANTS.split(",")
            if token.strip()
        ]

    async def get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client

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
                        await self._update_batch_progress(record=record, success=True)
            except Exception:
                await self._update_batch_progress(record=record, success=False)
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

    async def _fetch_next_job(self, job_type: str) -> Optional[Dict[str, Any]]:
        client = await self.get_client()
        response = await client.rpc("fetch_next_job", {"p_job_type": str(job_type)}).execute()
        jobs = response.data if isinstance(response.data, list) else []
        if not jobs:
            return None
        first = jobs[0]
        return first if isinstance(first, dict) else None

    async def _load_source_document(self, source_document_id: str) -> Optional[Dict[str, Any]]:
        client = await self.get_client()
        response = (
            await client.table("source_documents")
            .select("*")
            .eq("id", str(source_document_id))
            .maybe_single()
            .execute()
        )
        row = response.data
        return row if isinstance(row, dict) else None

    async def _mark_job_final(
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
        }
        if error_message:
            payload["error_message"] = error_message
        await client.table("job_queue").update(payload).eq("id", str(job_id)).execute()

    async def _run_single_ingestion_job(self, poller_id: int) -> bool:
        job = await self._fetch_next_job(job_type="ingest_document")
        if not job:
            return False

        job_id = str(job.get("id") or "")
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        source_document_id = str(payload.get("source_document_id") or "")
        if not source_document_id:
            logger.error("ingestion_job_invalid_payload job_id=%s payload=%s", job_id, payload)
            await self._mark_job_final(
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
        record = await self._load_source_document(source_document_id=source_document_id)
        if not record:
            await self._mark_job_final(
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
            await self._mark_job_final(
                job_id=job_id,
                status="completed",
                result={
                    "ok": True,
                    "source_document_id": source_document_id,
                    "final_status": status,
                },
            )
            return True

        await self._mark_job_final(
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
        return True

    async def _run_single_enrichment_job(self, poller_id: int) -> bool:
        job = await self._fetch_next_job(job_type="enrich_document")
        if not job:
            return False

        job_id = str(job.get("id") or "")
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        source_document_id = str(payload.get("source_document_id") or "")
        if not source_document_id:
            await self._mark_job_final(
                job_id=job_id,
                status="failed",
                error_message="Missing source_document_id in payload",
                result={"ok": False, "reason": "invalid_payload", "payload": payload},
            )
            return True

        record = await self._load_source_document(source_document_id=source_document_id)
        if not record:
            await self._mark_job_final(
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
        logger.info(
            "poller=%s processing_enrichment_job job_id=%s source_document_id=%s",
            poller_id,
            job_id,
            source_document_id,
        )
        try:
            result = await self.process_use_case.post_ingestion_service.run_deferred_enrichment(
                doc_id=source_document_id,
                tenant_id=str(tenant_id) if tenant_id else None,
                collection_id=str(collection_id) if collection_id else None,
            )
            await self._mark_job_final(
                job_id=job_id,
                status="completed",
                result=result,
            )
        except Exception as exc:
            await self._mark_job_final(
                job_id=job_id,
                status="failed",
                error_message=str(exc),
                result={
                    "ok": False,
                    "reason": "enrichment_failed",
                    "source_document_id": source_document_id,
                    "error": str(exc),
                },
            )
        return True

    async def _poller_loop(self, poller_id: int) -> None:
        logger.info("Ingestion poller started id=%s", poller_id)
        while self.is_running:
            try:
                processed = await self._run_single_ingestion_job(poller_id=poller_id)
                if not processed:
                    await asyncio.sleep(self.worker_poll_interval_seconds)
            except Exception as exc:
                logger.error("poller=%s ingestion_loop_error=%s", poller_id, exc, exc_info=True)
                await asyncio.sleep(self.worker_poll_interval_seconds)

    async def _enrichment_poller_loop(self, poller_id: int) -> None:
        logger.info("Enrichment poller started id=%s", poller_id)
        while self.is_running:
            try:
                processed = await self._run_single_enrichment_job(poller_id=poller_id)
                if not processed:
                    await asyncio.sleep(self.worker_poll_interval_seconds)
            except Exception as exc:
                logger.error("poller=%s enrichment_loop_error=%s", poller_id, exc, exc_info=True)
                await asyncio.sleep(self.worker_poll_interval_seconds)

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
    def _parse_iso_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

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
                    .limit(self.worker_tenant_queue_sample_limit)
                    .execute()
                )
                rows = queue_res.data or []
                queue_depth = len(rows)
                oldest_created_at = (
                    rows[0].get("created_at") if rows and isinstance(rows[0], dict) else None
                )
                oldest_dt = self._parse_iso_datetime(oldest_created_at)
                wait_seconds = 0
                if oldest_dt is not None:
                    wait_seconds = max(
                        0, int((datetime.now(timezone.utc) - oldest_dt).total_seconds())
                    )

                metrics["queue_depth"] = queue_depth
                metrics["queue_wait_seconds"] = wait_seconds
                metrics["queue_depth_sample_limit"] = self.worker_tenant_queue_sample_limit
                metrics["queue_depth_truncated"] = (
                    queue_depth >= self.worker_tenant_queue_sample_limit
                )

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

    @staticmethod
    def _resolve_tenant_key(record: Dict[str, Any]) -> str:
        tenant_id = record.get("institution_id")
        if tenant_id:
            return str(tenant_id)
        metadata = record.get("metadata")
        if isinstance(metadata, dict) and metadata.get("institution_id"):
            return str(metadata.get("institution_id"))
        return "global"

    async def _update_batch_progress(self, record: Dict[str, Any], success: bool) -> None:
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

    async def start(self):
        await self.get_client()
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

        scheduler_task: Optional[asyncio.Task] = None

        async def scheduler_loop() -> None:
            while self.is_running:
                if self.community_rebuild_enabled:
                    await self._tick_community_rebuild_scheduler()
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

    async def _resolve_tenant_ids_for_rebuild(self) -> list[str]:
        if self.community_rebuild_tenants:
            return self.community_rebuild_tenants

        client = await self.get_client()
        try:
            response = (
                await client.table("knowledge_entities").select("tenant_id").limit(10000).execute()
            )
            rows = response.data or []
            tenant_ids = sorted({str(row.get("tenant_id")) for row in rows if row.get("tenant_id")})
            return tenant_ids
        except Exception as exc:
            logger.warning("Could not auto-resolve tenants for community rebuild: %s", exc)
            return []

    async def _pick_audit_document_id(self, tenant_id: str) -> str | None:
        client = await self.get_client()
        try:
            response = (
                await client.table("source_documents")
                .select("id")
                .eq("institution_id", tenant_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                return None
            return str(rows[0].get("id"))
        except Exception as exc:
            logger.warning("Could not resolve audit document for tenant=%s: %s", tenant_id, exc)
            return None

    async def _audit_community_run(
        self, tenant_id: str, payload: Dict[str, Any], status: str
    ) -> None:
        doc_id = await self._pick_audit_document_id(tenant_id)
        if not doc_id:
            logger.warning(
                "Skipping ingestion_events audit for tenant=%s (no source document found)",
                tenant_id,
            )
            return

        message = (
            f"Community rebuild tenant={tenant_id}: "
            f"ok={payload.get('ok', False)}, "
            f"detected={payload.get('communities_detected', 0)}, "
            f"persisted={payload.get('communities_persisted', 0)}"
        )
        await self.source_repository.log_event(
            doc_id=doc_id,
            message=message,
            status=status,
            tenant_id=tenant_id,
            metadata={
                "phase": "community_rebuild",
                "tenant_id": tenant_id,
                "run_at": datetime.now(timezone.utc).isoformat(),
                "result": payload,
            },
        )

    async def _run_community_rebuild_cycle(self) -> None:
        tenant_ids = await self._resolve_tenant_ids_for_rebuild()
        if not tenant_ids:
            logger.info("Community scheduler: no tenants to rebuild")
            return

        logger.info("Community scheduler: starting cycle tenants=%s", len(tenant_ids))
        for tenant_id in tenant_ids:
            await self._enqueue_community_rebuild_job(tenant_id)

    async def _enqueue_community_rebuild_job(self, tenant_id: str) -> None:
        client = await self.get_client()
        try:
            existing = (
                await client.table("job_queue")
                .select("id,status")
                .eq("job_type", "community_rebuild")
                .eq("tenant_id", tenant_id)
                .in_("status", ["pending", "processing"])
                .limit(1)
                .execute()
            )

            if existing.data:
                logger.info(
                    "community_rebuild_job_exists",
                    tenant_id=tenant_id,
                    job_id=existing.data[0].get("id"),
                )
                return

            inserted = (
                await client.table("job_queue")
                .insert(
                    {
                        "job_type": "community_rebuild",
                        "tenant_id": tenant_id,
                        "payload": {"tenant_id": tenant_id, "scheduled_by": "ingestion_worker"},
                    }
                )
                .execute()
            )

            job_id = (inserted.data or [{}])[0].get("id")
            logger.info("community_rebuild_job_enqueued", tenant_id=tenant_id, job_id=job_id)
        except Exception as exc:
            logger.error(
                "community_rebuild_job_enqueue_failed", tenant_id=tenant_id, error=str(exc)
            )

    async def _tick_community_rebuild_scheduler(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_community_rebuild_at is not None:
            next_run = self._last_community_rebuild_at + timedelta(
                seconds=self.community_rebuild_interval_seconds
            )
            if now < next_run:
                return

        if self._community_rebuild_lock.locked():
            return

        async with self._community_rebuild_lock:
            self._last_community_rebuild_at = now
            try:
                await self._run_community_rebuild_cycle()
            except Exception as exc:
                logger.error("Community scheduler cycle failed: %s", exc, exc_info=True)


if __name__ == "__main__":
    # Ensure logs are visible
    worker = IngestionWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
