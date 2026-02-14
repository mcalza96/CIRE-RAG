"""Resilience tests for Visual Anchor fallback paths."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest


class _Response:
    def __init__(self, data: Any = None, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _FakeTable:
    def __init__(self, name: str, db: dict[str, list[dict[str, Any]]], fail_visual_insert: bool = False) -> None:
        self.name = name
        self.db = db
        self.fail_visual_insert = fail_visual_insert
        self._filters: list[tuple[str, Any]] = []
        self._payload: dict[str, Any] | None = None
        self._mode = "select"

    def select(self, _fields: str, count: str | None = None):
        self._mode = "select"
        self._count = count
        return self

    def eq(self, key: str, value: Any):
        self._filters.append((key, value))
        return self

    def limit(self, _n: int):
        return self

    def update(self, payload: dict[str, Any]):
        self._mode = "update"
        self._payload = payload
        return self

    def insert(self, payload: dict[str, Any]):
        self._mode = "insert"
        self._payload = payload
        return self

    async def execute(self) -> _Response:
        rows = self.db.setdefault(self.name, [])
        if self._mode == "select":
            filtered = [row for row in rows if all(row.get(k) == v for k, v in self._filters)]
            return _Response(filtered)

        if self._mode == "update":
            for row in rows:
                if all(row.get(k) == v for k, v in self._filters):
                    row.update(self._payload or {})
            return _Response([])

        if self._mode == "insert":
            if self.name == "visual_nodes" and self.fail_visual_insert:
                raise RuntimeError("visual_nodes insert failed")
            rows.append(dict(self._payload or {}))
            return _Response([self._payload])

        return _Response([])


class _FakeStorageBucket:
    async def upload(self, **_kwargs):
        return {"ok": True}

    async def remove(self, _paths: list[str]):
        return {"ok": True}

    async def get_public_url(self, path: str):
        return {"publicUrl": f"https://example.local/{path}"}


class _FakeStorage:
    def from_(self, _bucket: str) -> _FakeStorageBucket:
        return _FakeStorageBucket()


class _FakeRPC:
    async def execute(self):
        raise RuntimeError("forced rpc failure")


class _FakeSupabaseClient:
    def __init__(self, db: dict[str, list[dict[str, Any]]], fail_visual_insert: bool = False) -> None:
        self._db = db
        self._fail_visual_insert = fail_visual_insert
        self.storage = _FakeStorage()

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name=name, db=self._db, fail_visual_insert=self._fail_visual_insert)

    def rpc(self, _name: str, _payload: dict[str, Any]) -> _FakeRPC:
        return _FakeRPC()


@dataclass
class _FakeParseResult:
    dense_summary: str
    markdown_content: str
    visual_metadata: dict[str, Any]


class _FakeEmbeddingService:
    async def embed_texts(self, _texts, task: str = "retrieval.passage", mode: str | None = None):
        _ = task, mode
        return [[0.001] + [0.0] * 1023]


def _stub_heavy_modules_for_worker() -> None:
    """Stub heavy imports required by process_document_worker_use_case."""

    fake_llm = types.ModuleType("app.core.llm")
    fake_llm.get_llm = lambda **_kwargs: None
    sys.modules.setdefault("app.core.llm", fake_llm)

    fake_graph_extractor = types.ModuleType("app.services.knowledge.graph_extractor")
    fake_graph_extractor.GraphExtractor = object
    sys.modules.setdefault("app.services.knowledge.graph_extractor", fake_graph_extractor)

    fake_graph_repo = types.ModuleType("app.infrastructure.repositories.supabase_graph_repository")
    fake_graph_repo.SupabaseGraphRepository = object
    sys.modules.setdefault("app.infrastructure.repositories.supabase_graph_repository", fake_graph_repo)

    fake_dispatcher = types.ModuleType("app.workflows.ingestion.dispatcher")
    fake_dispatcher.IngestionDispatcher = object
    sys.modules.setdefault("app.workflows.ingestion.dispatcher", fake_dispatcher)

    fake_correlation = types.ModuleType("app.core.observability.correlation")
    fake_correlation.set_correlation_id = lambda *_args, **_kwargs: None
    fake_correlation.get_correlation_id = lambda *_args, **_kwargs: "test-correlation"
    sys.modules.setdefault("app.core.observability.correlation", fake_correlation)

    fake_logger_config = types.ModuleType("app.core.observability.logger_config")
    fake_logger_config.bind_context = lambda *_args, **_kwargs: None
    sys.modules.setdefault("app.core.observability.logger_config", fake_logger_config)


def _stub_embedding_service_module() -> None:
    """Stub embedding service module so imports do not require cloud/local backends."""

    fake_embedding = types.ModuleType("app.services.embedding_service")

    class _JinaEmbeddingService:
        @classmethod
        def get_instance(cls):
            return _FakeEmbeddingService()

    fake_embedding.JinaEmbeddingService = _JinaEmbeddingService
    sys.modules.setdefault("app.services.embedding_service", fake_embedding)


@pytest.mark.asyncio
async def test_integrator_uses_db_fallback_when_rpc_fails(tmp_path: Path) -> None:
    """If RPC fails, integrator should still stitch parent + visual node via fallback."""

    # Avoid importing heavy local embedding stack dependencies.
    sys.modules.setdefault("torch", types.ModuleType("torch"))
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.AutoModel = object
    fake_transformers.AutoTokenizer = object
    sys.modules.setdefault("transformers", fake_transformers)
    _stub_embedding_service_module()

    from app.services.ingestion.integrator import VisualGraphIntegrator

    parent_id = str(uuid4())
    db: dict[str, list[dict[str, Any]]] = {
        "content_chunks": [{"id": parent_id, "content": "Parent chunk base text."}],
        "visual_nodes": [],
    }
    client = _FakeSupabaseClient(db=db)

    image_path = tmp_path / "table.png"
    image_path.write_bytes(b"png-bytes")

    integrator = VisualGraphIntegrator(
        embedding_service=_FakeEmbeddingService(),
        supabase_client=client,
    )

    result = await integrator.integrate_visual_node(
        parent_chunk_id=parent_id,
        parent_chunk_text="Parent chunk base text.",
        image_path=image_path,
        parse_result=_FakeParseResult(
            dense_summary="Tabla de capitulos ISO.",
            markdown_content="| Norma | Capitulos |\n|---|---|\n| ISO 10005 | 4,5,6,7,8 |",
            visual_metadata={"type": "table"},
        ),
        content_type="table",
        metadata={"page": 1},
    )

    assert result.parent_table == "content_chunks"
    assert len(db["visual_nodes"]) == 1
    assert "VISUAL_ANCHOR" in db["content_chunks"][0]["content"]


@pytest.mark.asyncio
async def test_worker_inline_fallback_when_visual_integration_raises(monkeypatch) -> None:
    """Worker should persist inline visual fallback when integrator fails."""

    _stub_heavy_modules_for_worker()
    _stub_embedding_service_module()

    from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase

    class _Dummy:
        pass

    class _DummyStateManager:
        async def log_step(self, *_args, **_kwargs):
            return None

    # Fake DB client used by _persist_chunk_content_fallback and parent existence check.
    parent_id = str(uuid4())
    db = {"content_chunks": [{"id": parent_id, "content": "Texto base."}]}
    client = _FakeSupabaseClient(db=db)

    async def _fake_get_client():
        return client

    import app.application.use_cases.process_document_worker_use_case as worker_module

    monkeypatch.setattr("app.application.services.visual_anchor_service.get_async_supabase_client", _fake_get_client)

    use_case = ProcessDocumentWorkerUseCase(
        repository=_Dummy(),
        content_repo=_Dummy(),
        storage_service=_Dummy(),
        dispatcher=_Dummy(),
        taxonomy_manager=_Dummy(),
        metadata_adapter=_Dummy(),
        policy=_Dummy(),
        state_manager=_DummyStateManager(),
    )

    class _Parser:
        async def parse_image(self, **_kwargs):
            return _FakeParseResult(
                dense_summary="Resumen",
                markdown_content="| A | B |\n|---|---|\n| 1 | 2 |",
                visual_metadata={"type": "table"},
            )

    class _FailIntegrator:
        async def integrate_visual_node(self, **_kwargs):
            raise RuntimeError("forced integration failure")

    use_case.visual_parser = _Parser()
    use_case.visual_integrator = _FailIntegrator()

    result = types.SimpleNamespace(
        metadata={
            "routing": {
                "visual_tasks": [
                    {
                        "page": 1,
                        "content_type": "table",
                        "image_path": "/tmp/fake.png",
                        "metadata": {},
                    }
                ]
            }
        },
        chunks=[{"id": parent_id, "content": "Texto base.", "file_page_number": 1}],
    )

    stats = await use_case._run_visual_anchor_if_needed(doc_id="doc-1", tenant_id="tenant-1", result=result)

    updated = db["content_chunks"][0]["content"]
    assert "<visual_fallback" in updated
    assert "| A | B |" in updated
    assert stats["attempted"] == 1
    assert stats["degraded_inline"] == 1
    assert stats["parse_failed"] == 0


@pytest.mark.asyncio
async def test_worker_inline_fallback_when_visual_parse_raises(monkeypatch) -> None:
    """Worker should degrade inline when visual parsing fails."""

    _stub_heavy_modules_for_worker()
    _stub_embedding_service_module()

    from app.application.use_cases.process_document_worker_use_case import ProcessDocumentWorkerUseCase

    class _Dummy:
        pass

    class _DummyStateManager:
        async def log_step(self, *_args, **_kwargs):
            return None

    parent_id = str(uuid4())
    db = {"content_chunks": [{"id": parent_id, "content": "Texto base."}]}
    client = _FakeSupabaseClient(db=db)

    async def _fake_get_client():
        return client

    import app.application.use_cases.process_document_worker_use_case as worker_module

    monkeypatch.setattr("app.application.services.visual_anchor_service.get_async_supabase_client", _fake_get_client)

    use_case = ProcessDocumentWorkerUseCase(
        repository=_Dummy(),
        content_repo=_Dummy(),
        storage_service=_Dummy(),
        dispatcher=_Dummy(),
        taxonomy_manager=_Dummy(),
        metadata_adapter=_Dummy(),
        policy=_Dummy(),
        state_manager=_DummyStateManager(),
    )

    class _FailParser:
        async def parse_image(self, **_kwargs):
            raise RuntimeError("forced parse failure")

    class _UnusedIntegrator:
        async def integrate_visual_node(self, **_kwargs):
            raise AssertionError("integrator should not be called when parse fails")

    use_case.visual_parser = _FailParser()
    use_case.visual_integrator = _UnusedIntegrator()

    result = types.SimpleNamespace(
        metadata={
            "routing": {
                "visual_tasks": [
                    {
                        "page": 1,
                        "content_type": "table",
                        "image_path": "/tmp/fake.png",
                        "metadata": {},
                    }
                ]
            }
        },
        chunks=[{"id": parent_id, "content": "Texto base.", "file_page_number": 1}],
    )

    stats = await use_case._run_visual_anchor_if_needed(doc_id="doc-1", tenant_id="tenant-1", result=result)

    updated = db["content_chunks"][0]["content"]
    assert "<visual_fallback" in updated
    assert "[VISUAL_PARSE_UNAVAILABLE]" in updated
    assert stats["attempted"] == 1
    assert stats["degraded_inline"] == 1
    assert stats["parse_failed"] == 1
