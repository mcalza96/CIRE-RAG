from __future__ import annotations

from typing import Any, cast

import pytest

from app.application.services.query_decomposer import QueryDecomposer


class _BadLLM:
    async def ainvoke(self, _messages):
        return "not-json"


class _GoodLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, _messages):
        return self._content


@pytest.mark.asyncio
async def test_query_decomposer_uses_deterministic_fallback_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "app.application.services.query_decomposer.settings.QUERY_DECOMPOSER_MAX_SUBQUERIES", 3
    )
    decomposer = QueryDecomposer(llm_provider=cast(Any, _BadLLM()), timeout_ms=1000)

    plan = await decomposer.decompose(
        "Compara ISO 45001 8.1.2 con ISO 14001 8.1 y el impacto en ISO 9001 8.5.1"
    )

    assert plan.fallback_reason == "deterministic_error"
    assert len(plan.sub_queries) <= 3
    assert any("ISO 45001" in item.query for item in plan.sub_queries)
    assert any("ISO 14001" in item.query for item in plan.sub_queries)


@pytest.mark.asyncio
async def test_query_decomposer_applies_multihop_tolerance_gate(monkeypatch):
    monkeypatch.setattr(
        "app.application.services.query_decomposer.settings.QUERY_DECOMPOSER_MULTIHOP_TOLERANCE",
        0.9,
    )
    payload = (
        '{"is_multihop": true, "execution_mode": "parallel", "sub_queries": '
        '[{"id": 1, "query": "Que exige ISO 9001 5.1"}, '
        '{"id": 2, "query": "Evidencia complementaria de liderazgo"}]}'
    )
    decomposer = QueryDecomposer(llm_provider=cast(Any, _GoodLLM(payload)), timeout_ms=1000)

    plan = await decomposer.decompose("Que exige ISO 9001 5.1?")

    assert plan.is_multihop is False
    assert len(plan.sub_queries) == 1
    assert plan.fallback_reason == "multihop_below_tolerance"
