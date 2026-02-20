from __future__ import annotations

from typing import Any, cast

import pytest

from app.domain.schemas.query_plan import PlannedSubQuery, QueryPlan
from app.core.settings import settings
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine


class _DummyEmbeddingService:
    async def embed_texts(self, texts, task="retrieval.query"):
        return [[0.0] * 4 for _ in texts]


def test_scope_context_for_subquery_drops_ambiguous_clause() -> None:
    engine = AtomicRetrievalEngine(embedding_service=cast(Any, _DummyEmbeddingService()))
    scope_context = {
        "tenant_id": "t1",
        "filters": {"source_standard": "ISO 14001"},
    }
    out = engine._scope_context_for_subquery(  # noqa: SLF001
        scope_context=scope_context,
        subquery_text="ISO 14001 8.1.2 8.1 8.5.1 control operacional",
    )

    assert isinstance(out, dict)
    nested = out.get("filters")
    assert isinstance(nested, dict)
    metadata = nested.get("metadata")
    if isinstance(metadata, dict):
        assert "clause_id" not in metadata


def test_scope_context_for_subquery_keeps_single_clause() -> None:
    engine = AtomicRetrievalEngine(embedding_service=cast(Any, _DummyEmbeddingService()))
    scope_context = {
        "tenant_id": "t1",
        "filters": {"source_standard": "ISO 9001"},
    }
    out = engine._scope_context_for_subquery(  # noqa: SLF001
        scope_context=scope_context,
        subquery_text="ISO 9001 8.5.1 validacion del proceso",
    )

    assert isinstance(out, dict)
    nested = out.get("filters")
    assert isinstance(nested, dict)
    metadata = nested.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("clause_id") == "8.5.1"


@pytest.mark.asyncio
async def test_retrieve_context_from_plan_limits_branch_expansion(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_PLAN_MAX_BRANCH_EXPANSIONS", 2)

    engine = AtomicRetrievalEngine(embedding_service=cast(Any, _DummyEmbeddingService()))
    called_queries: list[str] = []

    async def fake_retrieve_context(*, query: str, **_: Any) -> list[dict[str, Any]]:
        called_queries.append(query)
        return []

    monkeypatch.setattr(engine, "retrieve_context", fake_retrieve_context)

    plan = QueryPlan(
        is_multihop=True,
        execution_mode="parallel",
        sub_queries=[
            PlannedSubQuery(id=1, query="q1"),
            PlannedSubQuery(id=2, query="q2"),
            PlannedSubQuery(id=3, query="q3"),
        ],
    )

    await engine.retrieve_context_from_plan(
        query="root",
        plan=plan,
        scope_context={"tenant_id": "t1"},
    )

    assert called_queries.count("root") == 1
    assert "q1" in called_queries
    assert "q2" in called_queries
    assert "q3" not in called_queries


@pytest.mark.asyncio
async def test_retrieve_context_from_plan_early_exit_on_scope_penalty(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_PLAN_MAX_BRANCH_EXPANSIONS", 3)
    monkeypatch.setattr(settings, "RETRIEVAL_PLAN_EARLY_EXIT_SCOPE_PENALTY", 0.8)

    engine = AtomicRetrievalEngine(embedding_service=cast(Any, _DummyEmbeddingService()))
    called_queries: list[str] = []

    async def fake_retrieve_context(*, query: str, **_: Any) -> list[dict[str, Any]]:
        called_queries.append(query)
        if query == "q1":
            return [
                {
                    "id": "x1",
                    "content": "out scope",
                    "metadata": {"source_standard": "ISO 14001"},
                }
            ]
        if query == "root":
            return [
                {
                    "id": "root1",
                    "content": "safe",
                    "metadata": {"source_standard": "ISO 9001"},
                }
            ]
        return [
            {
                "id": f"{query}-ok",
                "content": "in scope",
                "metadata": {"source_standard": "ISO 9001"},
            }
        ]

    monkeypatch.setattr(engine, "retrieve_context", fake_retrieve_context)

    plan = QueryPlan(
        is_multihop=True,
        execution_mode="sequential",
        sub_queries=[
            PlannedSubQuery(id=1, query="q1"),
            PlannedSubQuery(id=2, query="q2"),
            PlannedSubQuery(id=3, query="q3"),
        ],
    )

    await engine.retrieve_context_from_plan(
        query="root",
        plan=plan,
        scope_context={"tenant_id": "t1", "source_standard": "ISO 9001"},
    )

    assert called_queries == ["q1", "root"]
    assert engine.last_trace.get("plan_early_exit", {}).get("triggered") is True


def test_filter_structural_rows_drops_toc_and_frontmatter() -> None:
    rows = [
        {
            "id": "toc-1",
            "content": "9.1.2 Evaluacion ........ 14",
            "metadata": {"retrieval_eligible": False, "is_toc": True},
        },
        {
            "id": "front-1",
            "content": "Reservados los derechos de reproduccion.",
            "metadata": {"is_frontmatter": True},
        },
        {
            "id": "body-1",
            "content": "La organizacion debe definir criterios operacionales.",
            "metadata": {"retrieval_eligible": True, "is_normative_body": True},
        },
    ]
    kept, trace = AtomicRetrievalEngine._filter_structural_rows(rows)  # noqa: SLF001
    assert [item["id"] for item in kept] == ["body-1"]
    assert trace["dropped"] == 2
