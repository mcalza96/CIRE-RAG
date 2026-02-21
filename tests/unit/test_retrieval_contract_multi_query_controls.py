from __future__ import annotations

from typing import Any

import pytest

from app.api.v1.schemas.retrieval_advanced import (
    HybridTrace,
    HybridRetrievalResponse,
    MultiQueryRetrievalRequest,
    RetrievalItem,
    ScopeFilters,
    SubQueryRequest,
)
from app.services.retrieval.orchestration.contract_manager import ContractManager as RetrievalContractService
from app.infrastructure.settings import settings


@pytest.mark.asyncio
async def test_run_multi_query_skips_duplicate_scope_clause(monkeypatch) -> None:
    service = RetrievalContractService()
    calls: list[str] = []

    async def fake_run_hybrid(*args: Any, **kwargs: Any) -> HybridRetrievalResponse:
        req = args[0]
        calls.append(req.query)
        return HybridRetrievalResponse(
            items=[
                RetrievalItem(
                    source="C1",
                    content="ok",
                    score=0.8,
                    metadata={"row": {"id": req.query, "content": "ok", "tenant_id": "t1"}},
                )
            ],
            trace=HybridTrace(scope_penalized_ratio=0.0),
        )

    monkeypatch.setattr(service, "run_hybrid", fake_run_hybrid)

    request = MultiQueryRetrievalRequest(
        tenant_id="t1",
        collection_id="c1",
        queries=[
            SubQueryRequest(
                id="q1",
                query="ISO 9001 8.5.1 texto base",
                filters=ScopeFilters(source_standard="ISO 9001", metadata={"clause_id": "8.5.1"}),
            ),
            SubQueryRequest(
                id="q2",
                query="ISO 9001 8.5.1 texto alterno",
                filters=ScopeFilters(source_standard="ISO 9001", metadata={"clause_id": "8.5.1"}),
            ),
        ],
    )

    response = await service.run_multi_query(request)

    assert len(calls) == 1
    assert any(item.error_code == "SUBQUERY_SKIPPED_DUPLICATE" for item in response.subqueries)


@pytest.mark.asyncio
async def test_run_multi_query_drops_out_of_scope_branch(monkeypatch) -> None:
    service = RetrievalContractService()
    monkeypatch.setattr(settings, "RETRIEVAL_MULTI_QUERY_DROP_SCOPE_PENALIZED_BRANCHES", True)
    monkeypatch.setattr(settings, "RETRIEVAL_MULTI_QUERY_SCOPE_PENALTY_DROP_THRESHOLD", 0.95)

    async def fake_run_hybrid(*args: Any, **kwargs: Any) -> HybridRetrievalResponse:
        req = args[0]
        if "14001" in req.query:
            return HybridRetrievalResponse(
                items=[
                    RetrievalItem(
                        source="Cbad",
                        content="out",
                        score=0.1,
                        metadata={"row": {"id": "bad", "content": "out", "tenant_id": "t1"}},
                    )
                ],
                trace=HybridTrace(scope_penalized_ratio=1.0),
            )
        return HybridRetrievalResponse(
            items=[
                RetrievalItem(
                    source="Cgood",
                    content="in",
                    score=0.9,
                    metadata={"row": {"id": "good", "content": "in", "tenant_id": "t1"}},
                )
            ],
            trace=HybridTrace(scope_penalized_ratio=0.0),
        )

    monkeypatch.setattr(service, "run_hybrid", fake_run_hybrid)

    request = MultiQueryRetrievalRequest(
        tenant_id="t1",
        collection_id="c1",
        queries=[
            SubQueryRequest(id="q14001", query="consulta ISO 14001"),
            SubQueryRequest(id="q9001", query="consulta ISO 9001"),
        ],
    )

    response = await service.run_multi_query(request)

    assert any(item.error_code == "SUBQUERY_OUT_OF_SCOPE" for item in response.subqueries)
    assert any(item.source == "Cgood" for item in response.items)
    assert all(item.source != "Cbad" for item in response.items)
