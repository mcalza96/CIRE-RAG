"""Benchmark report generation for Visual Anchor RAG evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


@dataclass(frozen=True)
class BenchmarkSummary:
    """Aggregate metrics for a single pipeline run."""

    pipeline_name: str
    total_cases: int
    context_recall_avg: float
    faithfulness_avg: float
    latency_avg_sec: float
    total_cost_usd: float
    warnings: list[str]


def build_summary(pipeline_name: str, case_results: list[dict[str, Any]]) -> BenchmarkSummary:
    """Build aggregate benchmark metrics from per-case results."""

    if not case_results:
        return BenchmarkSummary(
            pipeline_name=pipeline_name,
            total_cases=0,
            context_recall_avg=0.0,
            faithfulness_avg=0.0,
            latency_avg_sec=0.0,
            total_cost_usd=0.0,
            warnings=["No cases executed."],
        )

    context_scores = [float(item.get("context_recall", 0.0)) for item in case_results]
    faith_scores = [float(item.get("faithfulness", 0.0)) for item in case_results]
    latency_values = [float(item.get("latency_sec", 0.0)) for item in case_results]
    cost_values = [float(item.get("cost_usd", 0.0)) for item in case_results]

    warnings = [
        f"Latency warning in case {item.get('case_id')}: {item.get('latency_sec'):.2f}s"
        for item in case_results
        if float(item.get("latency_sec", 0.0)) > 5.0
    ]

    if mean(faith_scores) < 0.9 and any(bool(item.get("is_visual_case")) for item in case_results):
        warnings.append("Sugerencia: Ajustar System Prompt del Generador para ser mas literal.")
    if mean(context_scores) < 0.9:
        warnings.append("Sugerencia: Mejorar la descripcion semantica (summary) generada por el VLM en la ingesta.")

    return BenchmarkSummary(
        pipeline_name=pipeline_name,
        total_cases=len(case_results),
        context_recall_avg=mean(context_scores),
        faithfulness_avg=mean(faith_scores),
        latency_avg_sec=mean(latency_values),
        total_cost_usd=sum(cost_values),
        warnings=warnings,
    )


def render_markdown_report(
    output_path: str | Path,
    summaries: list[BenchmarkSummary],
    case_results_by_pipeline: dict[str, list[dict[str, Any]]],
) -> Path:
    """Render benchmark report as Markdown."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()

    lines: list[str] = [
        "# CIRE-RAG Visual Anchor Benchmark Report",
        "",
        f"Generated at: `{ts}`",
        "",
        "## Aggregate Metrics",
        "",
        "| Pipeline | Cases | Context Recall | Faithfulness | Avg Latency (s) | Total Cost (USD) |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for summary in summaries:
        lines.append(
            f"| {summary.pipeline_name} | {summary.total_cases} | "
            f"{summary.context_recall_avg:.3f} | {summary.faithfulness_avg:.3f} | "
            f"{summary.latency_avg_sec:.3f} | {summary.total_cost_usd:.4f} |"
        )

    if len(summaries) == 2:
        baseline, visual = summaries
        context_delta = (visual.context_recall_avg - baseline.context_recall_avg) * 100
        faith_delta = (visual.faithfulness_avg - baseline.faithfulness_avg) * 100
        cost_delta = visual.total_cost_usd - baseline.total_cost_usd
        lines.extend(
            [
                "",
                "## A/B Delta",
                "",
                f"- Delta Score (Context Recall): `{context_delta:+.2f}%`",
                f"- Delta Score (Faithfulness): `{faith_delta:+.2f}%`",
                f"- Delta Cost: `${cost_delta:+.4f}`",
            ]
        )

    for summary in summaries:
        lines.extend(["", f"## Warnings / Suggestions - {summary.pipeline_name}"])
        if summary.warnings:
            lines.extend([f"- {warning}" for warning in summary.warnings])
        else:
            lines.append("- No warnings.")

    lines.extend(["", "## Per-Case Detail", ""])
    for pipeline_name, rows in case_results_by_pipeline.items():
        lines.extend([
            f"### {pipeline_name}",
            "",
            "| Case | Context Recall | Faithfulness | Latency (s) | Cost (USD) |",
            "|---|---:|---:|---:|---:|",
        ])
        for row in rows:
            lines.append(
                f"| {row.get('case_id')} | {float(row.get('context_recall', 0.0)):.3f} | "
                f"{float(row.get('faithfulness', 0.0)):.3f} | {float(row.get('latency_sec', 0.0)):.3f} | "
                f"{float(row.get('cost_usd', 0.0)):.4f} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_html_report(output_path: str | Path, markdown_report_path: str | Path) -> Path:
    """Render a minimal HTML report from generated markdown content."""

    html_path = Path(output_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_content = Path(markdown_report_path).read_text(encoding="utf-8")

    html = (
        "<html><head><meta charset='utf-8'><title>CIRE-RAG Benchmark</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:1rem;border-radius:8px;}"
        "</style></head><body>"
        "<h1>CIRE-RAG Visual Anchor Benchmark Report</h1>"
        f"<pre>{_escape_html(markdown_content)}</pre>"
        "</body></html>"
    )

    html_path.write_text(html, encoding="utf-8")
    return html_path


def _escape_html(content: str) -> str:
    """Escape unsafe html chars for preformatted report output."""

    return (
        content.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
