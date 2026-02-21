from __future__ import annotations

from typing import Any

import pytest

from app.api.v1.schemas.retrieval_advanced import (
    ComprehensiveRetrievalRequest,
    CoverageRequirements,
    HybridRetrievalResponse,
    HybridTrace,
    RetrievalItem,
    RetrievalPolicy,
    SearchHint,
)
from app.workflows.retrieval.contract_manager import ContractManager as RetrievalContractService


@pytest.mark.asyncio
async def test_run_comprehensive_applies_policy_and_reports_trace(monkeypatch) -> None:
    service = RetrievalContractService()
    captured: dict[str, Any] = {}

    async def fake_run_hybrid(request, *args, **kwargs) -> HybridRetrievalResponse:
        captured["query"] = request.query
        return HybridRetrievalResponse(
            items=[
                RetrievalItem(
                    source="C1",
                    content="|---|---|\ntexto [link](https://example.com)",
                    score=0.92,
                    metadata={"row": {"id": "1", "content": "x"}},
                ),
                RetrievalItem(
                    source="C2",
                    content="contenido de baja similitud",
                    score=0.31,
                    metadata={"row": {"id": "2", "content": "y"}},
                ),
            ],
            trace=HybridTrace(score_space="similarity"),
        )

    monkeypatch.setattr(service, "run_hybrid", fake_run_hybrid)

    req = ComprehensiveRetrievalRequest(
        query="consulta ec",
        tenant_id="t1",
        k=12,
        fetch_k=60,
        coverage_requirements=CoverageRequirements(),
        retrieval_policy=RetrievalPolicy(
            min_score=0.7,
            noise_reduction=True,
            search_hints=[SearchHint(term="ec", expand_to=["economia circular"])],
        ),
    )

    resp = await service.run_comprehensive(req)

    assert "economia circular" in str(captured.get("query") or "")
    assert len(resp.items) == 1
    assert resp.items[0].content == "texto link"
    assert resp.trace.retrieval_policy.get("min_score") == 0.7
    search_hints = resp.trace.retrieval_policy.get("search_hints_applied")
    assert isinstance(search_hints, dict)
    assert search_hints.get("applied") is True
