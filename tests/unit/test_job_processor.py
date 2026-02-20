import asyncio
from typing import Any, Dict, Optional

import pytest

from app.workflows.ingestion.job_processor import (
    SourceDocumentJobProcessor,
    TenantScopedJobProcessor,
)


class _TransientError(RuntimeError):
    pass


class _FakeJobStore:
    def __init__(self):
        self.requeue_calls: list[dict[str, str]] = []
        self._loads: list[Dict[str, Any] | Exception | None] = []

    def queue_load_results(self, *results: Dict[str, Any] | Exception | None) -> None:
        self._loads.extend(results)

    async def load_source_document(self, source_document_id: str) -> Optional[Dict[str, Any]]:
        if self._loads:
            item = self._loads.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"id": source_document_id}

    def is_transient_supabase_transport_error(self, exc: Exception) -> bool:
        return isinstance(exc, _TransientError)

    async def requeue_job_for_retry(self, *, job_id: str, error_message: str) -> None:
        self.requeue_calls.append({"job_id": job_id, "error_message": error_message})


def test_prepare_source_record_requires_source_document_id() -> None:
    async def _run() -> None:
        processor = SourceDocumentJobProcessor(
            job_store=_FakeJobStore(), max_source_lookup_requeues=2
        )
        with pytest.raises(ValueError, match="Missing source_document_id"):
            await processor.prepare_source_record(
                {"id": "job-1", "payload": {}},
                retry_on_transient=True,
            )

    asyncio.run(_run())


def test_prepare_source_record_requeues_on_transient_lookup_error() -> None:
    async def _run() -> None:
        store = _FakeJobStore()
        store.queue_load_results(_TransientError("temporary transport issue"))
        processor = SourceDocumentJobProcessor(job_store=store, max_source_lookup_requeues=2)

        with pytest.raises(asyncio.CancelledError):
            await processor.prepare_source_record(
                {"id": "job-2", "payload": {"source_document_id": "doc-2"}},
                retry_on_transient=True,
            )

        assert len(store.requeue_calls) == 1
        assert store.requeue_calls[0]["job_id"] == "job-2"
        assert "transient_lookup_attempt_1" in store.requeue_calls[0]["error_message"]

    asyncio.run(_run())


def test_prepare_source_record_returns_none_after_retry_budget_exhausted() -> None:
    async def _run() -> None:
        store = _FakeJobStore()
        store.queue_load_results(_TransientError("attempt-1"), _TransientError("attempt-2"))
        processor = SourceDocumentJobProcessor(job_store=store, max_source_lookup_requeues=1)

        with pytest.raises(asyncio.CancelledError):
            await processor.prepare_source_record(
                {"id": "job-3", "payload": {"source_document_id": "doc-3"}},
                retry_on_transient=True,
            )

        source_doc_id, payload, record = await processor.prepare_source_record(
            {"id": "job-3", "payload": {"source_document_id": "doc-3"}},
            retry_on_transient=True,
        )
        assert source_doc_id == "doc-3"
        assert payload["source_document_id"] == "doc-3"
        assert record is None

    asyncio.run(_run())


def test_prepare_tenant_job_accepts_root_tenant_id() -> None:
    job_id, tenant_id, payload = TenantScopedJobProcessor.prepare_tenant_job(
        {"id": "job-tenant-1", "tenant_id": "tenant-a", "payload": {"k": "v"}}
    )
    assert job_id == "job-tenant-1"
    assert tenant_id == "tenant-a"
    assert payload == {"k": "v"}


def test_prepare_tenant_job_accepts_payload_tenant_id() -> None:
    job_id, tenant_id, payload = TenantScopedJobProcessor.prepare_tenant_job(
        {"id": "job-tenant-2", "payload": {"tenant_id": "tenant-b"}}
    )
    assert job_id == "job-tenant-2"
    assert tenant_id == "tenant-b"
    assert payload == {"tenant_id": "tenant-b"}


def test_prepare_tenant_job_requires_tenant_id() -> None:
    with pytest.raises(ValueError, match="Missing tenant_id"):
        TenantScopedJobProcessor.prepare_tenant_job({"id": "job-tenant-3", "payload": {}})
