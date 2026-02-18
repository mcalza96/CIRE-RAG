import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / ".env.local", override=False)


@dataclass
class DocumentIngestionMetrics:
    source_document_id: str
    source_created_at: str
    status: str
    chunks_count: int
    first_chunk_at: Optional[str]
    tts_seconds: Optional[float]
    enrichment_job_created_at: Optional[str]
    enrichment_completed_at: Optional[str]
    enrichment_lag_seconds: Optional[float]
    e2e_enrichment_seconds: Optional[float]
    graph_calls_old_estimated: int
    graph_calls_new_estimated: int
    graph_cost_old_estimated: float
    graph_cost_new_estimated: float
    graph_cost_savings_estimated: float


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 3)


def _percentile(values: list[float], percentile: int) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return _round(ordered[0])
    idx = max(0, min(len(ordered) - 1, math.ceil((percentile / 100) * len(ordered)) - 1))
    return _round(ordered[idx])


async def _query_source_documents(client: Any, tenant_id: str, limit: int) -> list[dict[str, Any]]:
    response = (
        await client.table("source_documents")
        .select("id,created_at,status,institution_id")
        .eq("institution_id", tenant_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


async def _query_content_chunks(
    client: Any, source_ids: list[str]
) -> tuple[dict[str, int], dict[str, datetime]]:
    count_by_source: dict[str, int] = {sid: 0 for sid in source_ids}
    first_chunk_at_by_source: dict[str, datetime] = {}
    batch_size = 200

    for i in range(0, len(source_ids), batch_size):
        batch = source_ids[i : i + batch_size]
        response = (
            await client.table("content_chunks")
            .select("source_id,created_at")
            .in_("source_id", batch)
            .execute()
        )
        rows = response.data if isinstance(response.data, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or "").strip()
            if not source_id:
                continue
            count_by_source[source_id] = int(count_by_source.get(source_id, 0)) + 1
            chunk_dt = _parse_dt(row.get("created_at"))
            if chunk_dt is None:
                continue
            current = first_chunk_at_by_source.get(source_id)
            if current is None or chunk_dt < current:
                first_chunk_at_by_source[source_id] = chunk_dt

    return count_by_source, first_chunk_at_by_source


async def _query_enrichment_jobs(client: Any, tenant_id: str) -> dict[str, dict[str, datetime]]:
    response = (
        await client.table("job_queue")
        .select("status,payload,created_at,updated_at")
        .eq("job_type", "enrich_document")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=False)
        .limit(10000)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    by_source: dict[str, dict[str, datetime]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        source_document_id = str(payload.get("source_document_id") or "").strip()
        if not source_document_id:
            continue

        created_dt = _parse_dt(row.get("created_at"))
        updated_dt = _parse_dt(row.get("updated_at"))
        status = str(row.get("status") or "").strip().lower()
        slot = by_source.setdefault(source_document_id, {})

        if created_dt is not None:
            previous = slot.get("created")
            if previous is None or created_dt < previous:
                slot["created"] = created_dt

        if status == "completed" and updated_dt is not None:
            previous_done = slot.get("completed")
            if previous_done is None or updated_dt < previous_done:
                slot["completed"] = updated_dt

    return by_source


def _safe_delta_seconds(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    if delta < 0:
        return None
    return float(delta)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark ingestion TTS, enrichment lag, and Graph batching cost."
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("--limit", type=int, default=50, help="Max source documents to inspect")
    parser.add_argument(
        "--graph-batch-size",
        type=int,
        default=6,
        help="Batch size used by Graph extraction in current pipeline",
    )
    parser.add_argument(
        "--graph-call-unit-cost",
        type=float,
        default=0.0,
        help="Estimated cost per Graph LLM call in USD (for old/new comparison)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: rag/.logs/ingestion_bench_<ts>.json)",
    )
    args = parser.parse_args()

    from app.infrastructure.supabase.client import get_async_supabase_client

    started = time.perf_counter()
    client = await get_async_supabase_client()
    source_rows = await _query_source_documents(
        client=client, tenant_id=args.tenant_id, limit=max(1, args.limit)
    )

    source_ids = [str(row.get("id")) for row in source_rows]
    chunk_count_by_source, first_chunk_at_by_source = await _query_content_chunks(
        client=client, source_ids=source_ids
    )
    enrichment_by_source = await _query_enrichment_jobs(client=client, tenant_id=args.tenant_id)

    unit_cost = max(0.0, float(args.graph_call_unit_cost or 0.0))
    graph_batch_size = max(1, int(args.graph_batch_size or 1))
    metrics: list[DocumentIngestionMetrics] = []

    for row in source_rows:
        source_id = str(row.get("id"))
        source_created_dt = _parse_dt(row.get("created_at"))
        first_chunk_dt = first_chunk_at_by_source.get(source_id)
        enrich_slot = enrichment_by_source.get(source_id, {})
        enrich_created_dt = enrich_slot.get("created")
        enrich_completed_dt = enrich_slot.get("completed")

        chunks_count = int(chunk_count_by_source.get(source_id, 0))
        graph_calls_old = chunks_count
        graph_calls_new = int(math.ceil(chunks_count / graph_batch_size)) if chunks_count > 0 else 0
        graph_cost_old = graph_calls_old * unit_cost
        graph_cost_new = graph_calls_new * unit_cost

        tts_seconds = _safe_delta_seconds(source_created_dt, first_chunk_dt)
        enrichment_lag_seconds = _safe_delta_seconds(first_chunk_dt, enrich_completed_dt)
        e2e_enrichment_seconds = _safe_delta_seconds(source_created_dt, enrich_completed_dt)

        metrics.append(
            DocumentIngestionMetrics(
                source_document_id=source_id,
                source_created_at=(source_created_dt.isoformat() if source_created_dt else ""),
                status=str(row.get("status") or ""),
                chunks_count=chunks_count,
                first_chunk_at=(first_chunk_dt.isoformat() if first_chunk_dt else None),
                tts_seconds=_round(tts_seconds),
                enrichment_job_created_at=(
                    enrich_created_dt.isoformat() if enrich_created_dt else None
                ),
                enrichment_completed_at=(
                    enrich_completed_dt.isoformat() if enrich_completed_dt else None
                ),
                enrichment_lag_seconds=_round(enrichment_lag_seconds),
                e2e_enrichment_seconds=_round(e2e_enrichment_seconds),
                graph_calls_old_estimated=graph_calls_old,
                graph_calls_new_estimated=graph_calls_new,
                graph_cost_old_estimated=round(graph_cost_old, 6),
                graph_cost_new_estimated=round(graph_cost_new, 6),
                graph_cost_savings_estimated=round(graph_cost_old - graph_cost_new, 6),
            )
        )

    tts_values = [m.tts_seconds for m in metrics if m.tts_seconds is not None]
    lag_values = [m.enrichment_lag_seconds for m in metrics if m.enrichment_lag_seconds is not None]
    cost_old_total = sum(m.graph_cost_old_estimated for m in metrics)
    cost_new_total = sum(m.graph_cost_new_estimated for m in metrics)
    calls_old_total = sum(m.graph_calls_old_estimated for m in metrics)
    calls_new_total = sum(m.graph_calls_new_estimated for m in metrics)

    summary = {
        "generated_at": _utc_now_iso(),
        "tenant_id": args.tenant_id,
        "documents_scanned": len(metrics),
        "graph_batch_size": graph_batch_size,
        "graph_call_unit_cost": unit_cost,
        "time_to_searchable": {
            "count": len(tts_values),
            "p50_seconds": _percentile([float(x) for x in tts_values], 50),
            "p95_seconds": _percentile([float(x) for x in tts_values], 95),
            "avg_seconds": _round(sum(float(x) for x in tts_values) / len(tts_values))
            if tts_values
            else None,
        },
        "enrichment_lag": {
            "count": len(lag_values),
            "p50_seconds": _percentile([float(x) for x in lag_values], 50),
            "p95_seconds": _percentile([float(x) for x in lag_values], 95),
            "avg_seconds": _round(sum(float(x) for x in lag_values) / len(lag_values))
            if lag_values
            else None,
        },
        "graph_cost_estimation": {
            "old_calls_total": calls_old_total,
            "new_calls_total": calls_new_total,
            "old_cost_total": round(cost_old_total, 6),
            "new_cost_total": round(cost_new_total, 6),
            "savings_total": round(cost_old_total - cost_new_total, 6),
            "savings_pct": _round(((cost_old_total - cost_new_total) / cost_old_total) * 100.0)
            if cost_old_total > 0
            else None,
            "calls_reduction_pct": _round(
                ((calls_old_total - calls_new_total) / calls_old_total) * 100.0
            )
            if calls_old_total > 0
            else None,
        },
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "assumptions": {
            "time_to_searchable_uses_first_content_chunk_created_at": True,
            "enrichment_lag_uses_enrich_document_job_completed_timestamp": True,
            "cost_old_model_estimates_one_graph_call_per_chunk": True,
            "cost_new_model_estimates_ceil(chunks/graph_batch_size)": True,
        },
    }

    payload = {
        "summary": summary,
        "documents": [asdict(item) for item in metrics],
        "environment": {
            "cwd": os.getcwd(),
        },
    }

    out_dir = PROJECT_ROOT / ".logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.out) if args.out else (out_dir / f"ingestion_bench_{int(time.time())}.json")
    )
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
