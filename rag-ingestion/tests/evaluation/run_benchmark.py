"""Master benchmark runner for Visual Anchor RAG evaluation."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tests.evaluation.custom_metrics import TableCellExpectation, context_recall_score
from tests.evaluation.report_generator import (
    BenchmarkSummary,
    build_summary,
    render_html_report,
    render_markdown_report,
)


class RetrievalPipeline(Protocol):
    """Pipeline contract expected by benchmark runner."""

    async def answer(self, question: str, top_k: int = 10) -> dict[str, Any]:
        """Return dict with `answer`, `contexts`, optional `cost_usd`."""


@dataclass(frozen=True)
class EvaluationCase:
    """Single golden dataset row for benchmark execution."""

    case_id: str
    question: str
    ground_truth_answer: str
    expected_visual_ids: list[str]
    expected_table_markdown: str | None
    expected_key_cells: list[TableCellExpectation]
    is_visual_case: bool


async def main() -> None:
    """CLI entrypoint for benchmark execution."""

    parser = argparse.ArgumentParser(description="Run Visual Anchor benchmark suite")
    parser.add_argument("--dataset", required=True, help="Path to golden dataset JSON")
    parser.add_argument("--visual-adapter", required=True, help="Dotted path module:factory for visual pipeline")
    parser.add_argument("--baseline-adapter", required=False, help="Dotted path module:factory for baseline pipeline")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out-md", default="tests/evaluation/reports/benchmark_report.md")
    parser.add_argument("--out-html", default="tests/evaluation/reports/benchmark_report.html")
    args = parser.parse_args()

    dataset = _load_dataset(args.dataset)
    visual_pipeline = _load_pipeline(args.visual_adapter)

    by_pipeline: dict[str, list[dict[str, Any]]] = {}
    visual_rows = await _evaluate_pipeline("visual_anchor", visual_pipeline, dataset, top_k=args.top_k)
    by_pipeline["visual_anchor"] = visual_rows

    summaries: list[BenchmarkSummary] = [build_summary("visual_anchor", visual_rows)]

    if args.baseline_adapter:
        baseline_pipeline = _load_pipeline(args.baseline_adapter)
        baseline_rows = await _evaluate_pipeline("baseline_text", baseline_pipeline, dataset, top_k=args.top_k)
        by_pipeline["baseline_text"] = baseline_rows
        summaries.insert(0, build_summary("baseline_text", baseline_rows))

    md_path = render_markdown_report(args.out_md, summaries, by_pipeline)
    html_path = render_html_report(args.out_html, md_path)

    print(f"Benchmark complete. Markdown report: {md_path}")
    print(f"Benchmark complete. HTML report: {html_path}")


async def _evaluate_pipeline(
    pipeline_name: str,
    pipeline: RetrievalPipeline,
    cases: list[EvaluationCase],
    top_k: int,
) -> list[dict[str, Any]]:
    """Run all benchmark cases against a pipeline implementation."""

    rows: list[dict[str, Any]] = []
    for case in cases:
        t0 = time.perf_counter()
        response = await pipeline.answer(case.question, top_k=top_k)
        latency = time.perf_counter() - t0

        answer = str(response.get("answer", ""))
        contexts = response.get("contexts") or []
        context_recall = context_recall_score(contexts, case.expected_visual_ids)
        faithfulness = _faithfulness_score(answer=answer, contexts=contexts, ground_truth=case.ground_truth_answer)
        cost_usd = float(response.get("cost_usd", 0.0) or 0.0)

        row = {
            "pipeline": pipeline_name,
            "case_id": case.case_id,
            "context_recall": context_recall,
            "faithfulness": faithfulness,
            "latency_sec": latency,
            "cost_usd": cost_usd,
            "is_visual_case": case.is_visual_case,
        }
        if latency > 5.0:
            row["latency_warning"] = True
        rows.append(row)

    return rows


def _faithfulness_score(answer: str, contexts: list[dict[str, Any]], ground_truth: str) -> float:
    """Compute faithfulness using DeepEval when available, fallback to lexical grounding."""

    try:
        from deepeval.metrics import FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        context_texts = [str(item.get("content", "")) for item in contexts]
        test_case = LLMTestCase(
            input=ground_truth,
            actual_output=answer,
            retrieval_context=context_texts,
        )
        metric = FaithfulnessMetric(threshold=0.9)
        metric.measure(test_case)
        return float(metric.score)
    except Exception:
        answer_tokens = {token.lower() for token in answer.split() if len(token) > 3}
        context_tokens = {
            token.lower()
            for block in contexts
            for token in str(block.get("content", "")).split()
            if len(token) > 3
        }
        if not answer_tokens:
            return 0.0
        covered = sum(1 for token in answer_tokens if token in context_tokens)
        return covered / len(answer_tokens)


def _load_dataset(path: str) -> list[EvaluationCase]:
    """Load and validate benchmark dataset schema."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("cases", [])

    cases: list[EvaluationCase] = []
    for item in items:
        key_cells = [
            TableCellExpectation(
                row=str(cell.get("row", "")),
                column=str(cell.get("column", "")),
                value=str(cell.get("value", "")),
            )
            for cell in item.get("expected", {}).get("key_cells", [])
        ]

        cases.append(
            EvaluationCase(
                case_id=str(item.get("id", "unknown_case")),
                question=str(item.get("question", "")),
                ground_truth_answer=str(item.get("ground_truth_answer", "")),
                expected_visual_ids=[str(v) for v in item.get("expected", {}).get("visual_node_ids", [])],
                expected_table_markdown=item.get("expected", {}).get("markdown_table"),
                expected_key_cells=key_cells,
                is_visual_case=bool(item.get("expected", {}).get("is_visual", False)),
            )
        )

    return cases


def _load_pipeline(dotted: str) -> RetrievalPipeline:
    """Load a pipeline factory from module path `module:function` and instantiate."""

    if ":" not in dotted:
        raise ValueError("Adapter path must use module:function format.")

    module_name, factory_name = dotted.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, factory_name)
    pipeline = factory()
    if not hasattr(pipeline, "answer"):
        raise TypeError("Loaded pipeline does not expose async answer(question, top_k=...).")
    return pipeline


if __name__ == "__main__":
    asyncio.run(main())
