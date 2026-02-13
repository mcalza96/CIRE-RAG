"""Unit tests for atomic retrieval RPC wiring."""

from __future__ import annotations

import sys
import types
from typing import Any, cast

import pytest

# Stub embedding service before engine import.
_fake_embedding = types.ModuleType("app.services.embedding_service")


class _JinaStub:
    @classmethod
    def get_instance(cls):
        return _JinaStub()

    async def embed_texts(self, texts, task="retrieval.query"):
        return [[0.001] * 1024]


setattr(_fake_embedding, "JinaEmbeddingService", _JinaStub)
sys.modules.setdefault("app.services.embedding_service", _fake_embedding)

from app.services.retrieval.atomic_engine import AtomicRetrievalEngine


class _FakeRPCChain:
    """Mock for supabase client.rpc(...).execute() chain."""

    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self._data = data or []

    async def execute(self):
        return types.SimpleNamespace(data=self._data)


class _FakeClient:
    def __init__(self) -> None:
        self.last_rpc_name: str | None = None
        self.last_rpc_params: dict[str, Any] | None = None

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRPCChain:
        self.last_rpc_name = name
        self.last_rpc_params = params
        return _FakeRPCChain([])


@pytest.mark.asyncio
async def test_atomic_engine_passes_query_text_to_fts_rpc() -> None:
    """AtomicRetrievalEngine should pass query_text in FTS RPC params."""

    client = _FakeClient()
    engine = AtomicRetrievalEngine(
        embedding_service=cast(Any, _JinaStub()),
        supabase_client=client,
    )

    await engine._search_fts(query_text="tolerancia 0.05", source_ids=["s1"], fetch_k=10)

    assert client.last_rpc_name == "search_fts_only"
    assert client.last_rpc_params is not None
    assert client.last_rpc_params["query_text"] == "tolerancia 0.05"
    assert client.last_rpc_params["source_ids"] == ["s1"]


@pytest.mark.asyncio
async def test_atomic_engine_uses_vector_rpc() -> None:
    """AtomicRetrievalEngine should call search_vectors_only RPC."""

    client = _FakeClient()
    engine = AtomicRetrievalEngine(
        embedding_service=cast(Any, _JinaStub()),
        supabase_client=client,
    )

    await engine._search_vectors(query_vector=[0.1] * 1024, source_ids=["s1"], fetch_k=8)

    assert client.last_rpc_name == "search_vectors_only"
    assert client.last_rpc_params is not None
    assert client.last_rpc_params["source_ids"] == ["s1"]
