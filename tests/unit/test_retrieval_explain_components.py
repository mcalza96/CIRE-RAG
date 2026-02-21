import asyncio

from app.api.v1.schemas.retrieval_advanced import (
    ExplainRetrievalRequest,
    HybridRetrievalResponse,
    HybridTrace,
    RetrievalItem,
    ScopeFilters,
    TimeRangeFilter,
)
from app.services.retrieval.orchestration.contract_manager import ContractManager as RetrievalContractService


class _StubRetrievalContractService(RetrievalContractService):
    async def run_hybrid(self, request):  # type: ignore[override]
        return HybridRetrievalResponse(
            items=[
                RetrievalItem(
                    source="C1",
                    content="Clause evidence",
                    score=0.88,
                    metadata={
                        "row": {
                            "id": "doc-1",
                            "similarity": 0.81,
                            "source_layer": "vector",
                            "source_type": "content_chunk",
                            "collection_id": "col-1",
                            "created_at": "2026-01-15T00:00:00+00:00",
                            "scope_penalized": False,
                            "metadata": {
                                "tenant_id": "tenant-a",
                                "department": "qa",
                            },
                        }
                    },
                )
            ],
            trace=HybridTrace(
                filters_applied={"collection_id": "col-1"},
                engine_mode="atomic",
                planner_used=True,
                planner_multihop=False,
                fallback_used=False,
                timings_ms={"total": 9.2},
                warnings=[],
            ),
        )


def test_explain_returns_score_components_and_filter_matches() -> None:
    service = _StubRetrievalContractService()
    request = ExplainRetrievalRequest(
        query="Explain clause",
        tenant_id="tenant-a",
        collection_id="col-1",
        top_n=1,
        filters=ScopeFilters(
            metadata={"department": "qa"},
            time_range=TimeRangeFilter(field="created_at", **{"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"}),
        ),
    )

    response = asyncio.run(service.run_explain(request))

    assert len(response.items) == 1
    item = response.items[0]
    assert item.explain.score_components.base_similarity == 0.81
    assert item.explain.score_components.final_score == 0.88
    assert item.explain.retrieval_path.source_layer == "vector"
    assert item.explain.matched_filters.collection_id_match is True
    assert item.explain.matched_filters.time_range_match is True
    assert item.explain.matched_filters.metadata_keys_matched == ["department"]
