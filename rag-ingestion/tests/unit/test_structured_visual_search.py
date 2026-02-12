"""Unit tests for structured visual search via UnifiedRetrievalEngine."""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub embedding service before engine import.
_fake_embedding = types.ModuleType("app.services.embedding_service")


class _JinaStub:
    @classmethod
    def get_instance(cls):
        return _JinaStub()

    async def embed_texts(self, texts, task="retrieval.query"):
        return [[0.001] * 1024]


_fake_embedding.JinaEmbeddingService = _JinaStub
sys.modules.setdefault("app.services.embedding_service", _fake_embedding)

from app.services.retrieval.engine import UnifiedRetrievalEngine


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
async def test_engine_passes_query_text_to_rpc() -> None:
    """UnifiedRetrievalEngine should pass p_query_text in RPC params."""

    client = _FakeClient()
    engine = UnifiedRetrievalEngine(
        embedding_service=_JinaStub(),
        supabase_client=client,
    )

    await engine.retrieve_context(query="tolerancia 0.05")

    assert client.last_rpc_params is not None
    assert "p_query_text" in client.last_rpc_params
    assert client.last_rpc_params["p_query_text"] == "tolerancia 0.05"


@pytest.mark.asyncio
async def test_engine_defaults_to_v2_rpc() -> None:
    """Engine should default to unified_search_context_v2."""

    client = _FakeClient()
    engine = UnifiedRetrievalEngine(
        embedding_service=_JinaStub(),
        supabase_client=client,
    )

    await engine.retrieve_context(query="ISO 9001")

    assert client.last_rpc_name == "unified_search_context_v2"
