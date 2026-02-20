from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.knowledge.graph_extractor import (
    BatchChunkGraphExtraction,
    ChunkGraphExtraction,
    GraphExtractor,
    IndexedChunkGraphExtraction,
)


class _DummyStrictEngine:
    def __init__(self, response: BatchChunkGraphExtraction | None = None) -> None:
        self.calls = 0
        self._response = response or BatchChunkGraphExtraction(chunks=[])

    async def agenerate(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.calls += 1
        return self._response


@pytest.mark.asyncio
async def test_batch_guardrail_forces_per_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    strict_engine = _DummyStrictEngine()
    extractor = GraphExtractor(strict_engine=strict_engine)
    per_chunk = AsyncMock(return_value=ChunkGraphExtraction())
    monkeypatch.setattr(extractor, "extract_graph_from_chunk_async", per_chunk)

    texts = ["A" * 13000, "B" * 200]
    out = await extractor.extract_graph_batch_async(texts)

    assert len(out) == 2
    assert strict_engine.calls == 0
    assert per_chunk.await_count == 2


@pytest.mark.asyncio
async def test_batch_guardrail_allows_small_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    strict_engine = _DummyStrictEngine(
        response=BatchChunkGraphExtraction(
            chunks=[
                IndexedChunkGraphExtraction(chunk_index=1),
                IndexedChunkGraphExtraction(chunk_index=2),
            ]
        )
    )
    extractor = GraphExtractor(strict_engine=strict_engine)
    per_chunk = AsyncMock(return_value=ChunkGraphExtraction())
    monkeypatch.setattr(extractor, "extract_graph_from_chunk_async", per_chunk)

    monkeypatch.setattr(
        "app.services.knowledge.graph_extractor.settings.GRAPH_EXTRACTION_BATCH_MAX_CHARS", 5000
    )

    out = await extractor.extract_graph_batch_async(["hello", "world"])

    assert len(out) == 2
    assert strict_engine.calls == 1
    assert per_chunk.await_count == 0
