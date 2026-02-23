from __future__ import annotations

import pytest

from app.domain.ingestion.graph.graph_extractor import (
    ChunkGraphExtraction,
    Entity,
    GraphExtractor,
    Relation,
)


class _DummyStrictEngine:
    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self._fail_first = fail_first

    async def agenerate(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.calls += 1
        if self._fail_first and self.calls == 1:
            raise RuntimeError("429 rate limit")
        return ChunkGraphExtraction(
            entities=[Entity(name="A", type="DOC", description="desc")],
            relations=[
                Relation(
                    source="A",
                    target="A",
                    relation_type="SELF",
                    description="self",
                    weight=5,
                )
            ],
        )


@pytest.mark.asyncio
async def test_batch_extraction_runs_per_chunk() -> None:
    strict_engine = _DummyStrictEngine()
    extractor = GraphExtractor(strict_engine=strict_engine)

    texts = ["A" * 13000, "B" * 200]
    out = await extractor.extract_graph_batch_async(texts)

    assert len(out) == 2
    assert strict_engine.calls == 2
    assert out[0].relations == []
    assert out[1].relations == []


@pytest.mark.asyncio
async def test_batch_extraction_retries_retryable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch
    strict_engine = _DummyStrictEngine(fail_first=True)
    extractor = GraphExtractor(
        strict_engine=strict_engine,
        retry_max_attempts=2,
        retry_base_delay_seconds=0.01,
        retry_max_delay_seconds=0.01,
        retry_jitter_seconds=0.0,
    )

    out = await extractor.extract_graph_batch_async(["hello"])

    assert len(out) == 1
    assert strict_engine.calls == 2
    assert len(out[0].entities) == 1
