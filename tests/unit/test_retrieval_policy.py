from __future__ import annotations

from app.domain.retrieval_policy import (
    apply_search_hints,
    filter_rows_by_min_score,
    reduce_structural_noise_rows,
)


def test_apply_search_hints_expands_query() -> None:
    query, trace = apply_search_hints(
        "requisitos de ec",
        [{"term": "ec", "expand_to": ["economia circular", "ISO 14001"]}],
    )
    assert "economia circular" in query
    assert trace["applied"] is True
    assert trace["expanded_terms"]


def test_filter_rows_by_min_score_respects_rrf_score_space() -> None:
    rows = [
        {"content": "a", "score": 0.9, "similarity": 0.9, "metadata": {}},
        {"content": "b", "score": 0.5, "similarity": 0.5, "metadata": {}},
        {
            "content": "c",
            "score": 0.01,
            "similarity": 0.01,
            "metadata": {"score_space": "rrf"},
        },
    ]
    kept, trace = filter_rows_by_min_score(rows, min_score=0.7)
    kept_content = {str(item.get("content")) for item in kept}
    assert kept_content == {"a", "c"}
    assert trace["score_space_bypassed"] == 1


def test_reduce_structural_noise_rows_cleans_markdown_table_border() -> None:
    rows = [
        {
            "content": "|---|---|\n[Texto](https://example.com)   con  espacios\n",
            "score": 0.9,
            "metadata": {},
        }
    ]
    cleaned, trace = reduce_structural_noise_rows(rows)
    assert len(cleaned) == 1
    assert cleaned[0]["content"] == "Texto con espacios"
    assert trace["changed"] == 1


def test_reduce_structural_noise_rows_drops_structural_toc_rows() -> None:
    rows = [
        {
            "content": "9.1.2 Evaluacion del cumplimiento .......... 14\n10 Mejora .......... 15",
            "score": 0.91,
            "metadata": {"retrieval_eligible": False, "is_toc": True},
        },
        {
            "content": "La organizacion debe evaluar el cumplimiento de sus obligaciones.",
            "score": 0.75,
            "metadata": {"retrieval_eligible": True, "is_normative_body": True},
        },
    ]
    cleaned, trace = reduce_structural_noise_rows(rows)
    assert len(cleaned) == 1
    assert cleaned[0]["content"].startswith("La organizacion")
    assert trace["dropped_structural"] == 1
