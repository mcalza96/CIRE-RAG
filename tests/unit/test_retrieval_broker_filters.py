from typing import Any

from app.services.retrieval.orchestration.retrieval_broker import RetrievalBroker


class _DummyRepository:
    async def match_knowledge(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def match_knowledge_paginated(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def match_summaries(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def test_resolve_filters_prefers_scope_context_standards_over_query() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {
        "type": "institutional",
        "tenant_id": "tenant-a",
        "filters": {
            "source_standard": "ISO 14001",
            "source_standards": ["ISO 14001"],
            "metadata": {"clause_id": "7.4"},
        },
    }

    filters = broker._resolve_filters(  # noqa: SLF001
        "Que exige ISO 9001 7.5.3 y ISO 45001 5.4",
        scope_context,
    )

    assert filters["source_standard"] == "ISO 14001"
    assert "source_standards" not in filters


def test_resolve_filters_uses_query_standard_when_scope_context_has_none() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {"type": "institutional", "tenant_id": "tenant-a", "filters": {}}

    filters = broker._resolve_filters(  # noqa: SLF001
        "Que exige ISO 9001 7.5.3?",
        scope_context,
    )

    assert filters["source_standard"] == "ISO 9001"
    assert "source_standards" not in filters


def test_resolve_filters_uses_plural_only_for_multi_scope() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {"type": "institutional", "tenant_id": "tenant-a", "filters": {}}

    filters = broker._resolve_filters(  # noqa: SLF001
        "Compara ISO 9001 8.5.1 con ISO 14001 8.1",
        scope_context,
    )

    assert filters["source_standards"] == ["ISO 9001", "ISO 14001"]
    assert "source_standard" not in filters


def test_resolve_filters_drops_clause_hint_for_multi_scope_query() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {"type": "institutional", "tenant_id": "tenant-a", "filters": {}}

    filters = broker._resolve_filters(  # noqa: SLF001
        "Compara ISO 45001 8.1.2 con ISO 14001 8.1 e ISO 9001 8.5.1",
        scope_context,
    )

    nested_raw = filters.get("filters")
    nested: dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
    assert "clause_id" not in nested


def test_resolve_filters_skips_clause_when_multiple_clause_candidates_near_standard() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {
        "type": "institutional",
        "tenant_id": "tenant-a",
        "filters": {"source_standard": "ISO 14001"},
    }

    filters = broker._resolve_filters(  # noqa: SLF001
        "ISO 14001 8.1.2 8.1 8.5.1 requisitos de control operacional",
        scope_context,
    )

    metadata = filters.get("metadata")
    if isinstance(metadata, dict):
        assert "clause_id" not in metadata
    nested = filters.get("filters")
    if isinstance(nested, dict):
        assert "clause_id" not in nested


def test_resolve_filters_maps_clause_when_single_candidate_present() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {
        "type": "institutional",
        "tenant_id": "tenant-a",
        "filters": {"source_standard": "ISO 9001"},
    }

    filters = broker._resolve_filters(  # noqa: SLF001
        "ISO 9001 8.5.1 validacion de proceso",
        scope_context,
    )

    metadata = filters.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("clause_id") == "8.5.1"


def test_scope_penalty_accepts_iso_with_compact_format() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    rows = [
        {
            "id": "r1",
            "content": "dummy",
            "metadata": {"source_standard": "ISO14001:2015"},
            "similarity": 0.9,
            "score": 0.9,
        }
    ]

    reranked = broker._apply_scope_penalty(rows, ("ISO 14001",))  # noqa: SLF001

    assert len(reranked) == 1
    assert reranked[0].get("scope_penalized") is not True


def test_scope_penalty_accepts_numeric_only_standard() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    rows = [
        {
            "id": "r1",
            "content": "dummy",
            "metadata": {"source_standard": "14001"},
            "similarity": 0.9,
            "score": 0.9,
        }
    ]

    reranked = broker._apply_scope_penalty(rows, ("ISO 14001",))  # noqa: SLF001

    assert len(reranked) == 1
    assert reranked[0].get("scope_penalized") is not True
