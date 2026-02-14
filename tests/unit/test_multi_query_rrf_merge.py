from app.api.v1.schemas.retrieval_advanced import RetrievalItem
from app.application.services.retrieval_contract_service import RetrievalContractService


def _item(row_id: str, score: float) -> RetrievalItem:
    return RetrievalItem(
        source=f"src-{row_id}",
        content=f"content-{row_id}",
        score=score,
        metadata={"row": {"id": row_id, "metadata": {"tenant_id": "tenant-a"}}},
    )


def test_rrf_merge_is_deterministic_and_dedupes_by_row_id() -> None:
    grouped = [
        ("q1", [_item("doc-1", 0.95), _item("doc-2", 0.90)]),
        ("q2", [_item("doc-3", 0.92), _item("doc-1", 0.91)]),
    ]

    merged = RetrievalContractService._rrf_merge(grouped, rrf_k=60, top_k=5)

    assert [item.metadata["row"]["id"] for item in merged] == ["doc-1", "doc-3", "doc-2"]
    assert len(merged) == 3


def test_rrf_merge_respects_top_k() -> None:
    grouped = [
        ("q1", [_item("doc-1", 0.95), _item("doc-2", 0.90)]),
        ("q2", [_item("doc-3", 0.92), _item("doc-1", 0.91)]),
    ]

    merged = RetrievalContractService._rrf_merge(grouped, rrf_k=60, top_k=2)

    assert len(merged) == 2
    assert [item.metadata["row"]["id"] for item in merged] == ["doc-1", "doc-3"]
