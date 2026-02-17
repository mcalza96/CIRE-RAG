from typing import Any

from app.application.services.retrieval_broker import RetrievalBroker


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

    assert filters["source_standards"] == ["ISO 14001"]
    assert filters["source_standard"] == "ISO 14001"
    assert filters["source_standard"] in filters["source_standards"]


def test_resolve_filters_uses_query_standard_when_scope_context_has_none() -> None:
    broker = RetrievalBroker(repository=_DummyRepository())
    scope_context = {"type": "institutional", "tenant_id": "tenant-a", "filters": {}}

    filters = broker._resolve_filters(  # noqa: SLF001
        "Que exige ISO 9001 7.5.3?",
        scope_context,
    )

    assert filters["source_standards"] == ["ISO 9001"]
    assert filters["source_standard"] == "ISO 9001"
    assert filters["source_standard"] in filters["source_standards"]
