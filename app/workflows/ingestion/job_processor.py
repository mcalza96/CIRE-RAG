import asyncio
from typing import Any, Dict, Optional, Protocol

from app.core.observability.ingestion_logging import compact_error


class JobStoreProtocol(Protocol):
    async def load_source_document(self, source_document_id: str) -> Optional[Dict[str, Any]]: ...

    def is_transient_supabase_transport_error(self, exc: Exception) -> bool: ...

    async def requeue_job_for_retry(self, *, job_id: str, error_message: str) -> None: ...


class SourceDocumentJobProcessor:
    """Template processor for source-document based jobs.

    Encapsulates payload validation and transient source lookup retries/requeues.
    """

    def __init__(self, *, job_store: JobStoreProtocol, max_source_lookup_requeues: int):
        self.job_store = job_store
        self.max_source_lookup_requeues = max_source_lookup_requeues
        self._source_lookup_requeues: Dict[str, int] = {}

    async def prepare_source_record(
        self,
        job: Dict[str, Any],
        *,
        retry_on_transient: bool,
    ) -> tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
        job_id = str(job.get("id") or "")
        raw_payload = job.get("payload")
        payload: Dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        source_doc_id = str(payload.get("source_document_id") or "").strip()
        if not source_doc_id:
            raise ValueError("Missing source_document_id in payload")

        if retry_on_transient:
            record = await self._load_record_with_retries(job_id, source_doc_id)
        else:
            record = await self.job_store.load_source_document(source_doc_id)
        return source_doc_id, payload, record

    async def _load_record_with_retries(
        self, job_id: str, source_doc_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            record = await self.job_store.load_source_document(source_doc_id)
            self._source_lookup_requeues.pop(job_id, None)
            return record
        except Exception as exc:
            if not self.job_store.is_transient_supabase_transport_error(exc):
                raise

            attempts = self._source_lookup_requeues.get(job_id, 0) + 1
            self._source_lookup_requeues[job_id] = attempts

            if attempts > self.max_source_lookup_requeues:
                self._source_lookup_requeues.pop(job_id, None)
                return None

            await self.job_store.requeue_job_for_retry(
                job_id=job_id,
                error_message=f"transient_lookup_attempt_{attempts}: {compact_error(exc)}",
            )
            raise asyncio.CancelledError("Requeued due to transient lookup error")


class TenantScopedJobProcessor:
    """Template processor for jobs that require tenant context.

    Supports legacy job shapes where `tenant_id` can arrive at root-level
    or inside `payload`.
    """

    @staticmethod
    def prepare_tenant_job(job: Dict[str, Any]) -> tuple[str, str, Dict[str, Any]]:
        job_id = str(job.get("id") or "").strip()
        raw_payload = job.get("payload")
        payload: Dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        tenant_id = str(job.get("tenant_id") or payload.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError(f"Missing tenant_id in job {job_id}")
        return job_id, tenant_id, payload
