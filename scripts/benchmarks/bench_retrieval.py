import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class QueryRun:
    query: str
    duration_ms: float
    result_count: int
    ids: list[str]
    approx_result_json_bytes: int
    approx_content_bytes: int
    source_layers: dict[str, int]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queries(path: str | None, inline: list[str]) -> list[str]:
    queries: list[str] = [q.strip() for q in inline if q and q.strip()]
    if not path:
        return queries

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"queries file not found: {path}")

    if p.suffix.lower() in {".json"}:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, str) and item.strip():
                    queries.append(item.strip())
        elif isinstance(payload, dict) and isinstance(payload.get("queries"), list):
            for item in payload.get("queries"):
                if isinstance(item, str) and item.strip():
                    queries.append(item.strip())
        else:
            raise ValueError("queries JSON must be a list[str] or {queries: list[str]}")
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            queries.append(text)

    return queries


def _bucket_layers(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        layer = str(row.get("source_layer") or "unknown")
        out[layer] = out.get(layer, 0) + 1
    return out


def _approx_sizes(rows: list[dict[str, Any]]) -> tuple[int, int]:
    try:
        json_bytes = len(json.dumps(rows, default=str, ensure_ascii=True).encode("utf-8"))
    except Exception:
        json_bytes = -1
    content_bytes = 0
    for row in rows:
        content = row.get("content")
        if isinstance(content, str) and content:
            content_bytes += len(content.encode("utf-8"))
    return json_bytes, content_bytes


async def _run_once(
    *,
    engine: Any,
    query: str,
    scope_context: dict[str, Any],
    k: int,
    fetch_k: int,
) -> QueryRun:
    start = time.perf_counter()
    rows = await engine.retrieve_context(
        query=query, scope_context=scope_context, k=k, fetch_k=fetch_k
    )
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    ids = [str(r.get("id") or "") for r in rows if isinstance(r, dict) and r.get("id")]
    approx_json_bytes, approx_content_bytes = _approx_sizes(rows)
    return QueryRun(
        query=query,
        duration_ms=duration_ms,
        result_count=len(rows),
        ids=ids,
        approx_result_json_bytes=approx_json_bytes,
        approx_content_bytes=approx_content_bytes,
        source_layers=_bucket_layers(rows),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark retrieval pipeline (atomic engine).")
    parser.add_argument(
        "--tenant-id", required=True, help="Tenant UUID/string for scoping source_documents"
    )
    parser.add_argument("--collection-id", default=None, help="Optional collection_id filter")
    parser.add_argument("--queries", default=None, help="Path to .txt or .json query list")
    parser.add_argument("--query", action="append", default=[], help="Inline query (repeatable)")
    parser.add_argument("--k", type=int, default=10, help="Return top-k after merge")
    parser.add_argument("--fetch-k", type=int, default=40, help="Internal candidate pool size")
    parser.add_argument("--repeats", type=int, default=1, help="Repeat each query N times")
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: rag/.logs/retrieval_bench_<ts>.json)",
    )
    parser.add_argument(
        "--hybrid-rpc",
        choices=["default", "on", "off"],
        default="default",
        help="Override ATOMIC_USE_HYBRID_RPC for this run",
    )
    parser.add_argument(
        "--enable-fts",
        choices=["default", "on", "off"],
        default="default",
        help="Override ATOMIC_ENABLE_FTS for this run",
    )
    parser.add_argument(
        "--enable-graph",
        choices=["default", "on", "off"],
        default="default",
        help="Override ATOMIC_ENABLE_GRAPH_HOP for this run",
    )
    args = parser.parse_args()

    queries = _load_queries(args.queries, args.query)
    if not queries:
        raise SystemExit("no queries provided; pass --query or --queries")

    from app.core.settings import settings
    from app.core.observability.retrieval_metrics import retrieval_metrics_store
    from app.services.retrieval.atomic_engine import AtomicRetrievalEngine

    if args.hybrid_rpc != "default":
        settings.ATOMIC_USE_HYBRID_RPC = args.hybrid_rpc == "on"
    if args.enable_fts != "default":
        settings.ATOMIC_ENABLE_FTS = args.enable_fts == "on"
    if args.enable_graph != "default":
        settings.ATOMIC_ENABLE_GRAPH_HOP = args.enable_graph == "on"

    scope_context: dict[str, Any] = {
        "type": "institutional",
        "tenant_id": args.tenant_id,
    }
    if args.collection_id:
        scope_context["collection_id"] = args.collection_id

    engine = AtomicRetrievalEngine()
    runs: list[dict[str, Any]] = []

    bench_started = time.perf_counter()
    for query in queries:
        for i in range(max(1, int(args.repeats))):
            run = await _run_once(
                engine=engine,
                query=query,
                scope_context=scope_context,
                k=args.k,
                fetch_k=args.fetch_k,
            )
            payload = asdict(run)
            payload["repeat_index"] = i
            runs.append(payload)
            print(
                f"[{len(runs)}] {run.duration_ms}ms rows={run.result_count} layers={run.source_layers} :: {query[:80]}"
            )

    bench_ms = round((time.perf_counter() - bench_started) * 1000, 2)

    out_dir = PROJECT_ROOT / ".logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = (
        Path(args.out) if args.out else (out_dir / f"retrieval_bench_{int(time.time())}.json")
    )

    summary = {
        "generated_at": _utc_now_iso(),
        "workdir": os.getcwd(),
        "tenant_id": args.tenant_id,
        "collection_id": args.collection_id,
        "k": args.k,
        "fetch_k": args.fetch_k,
        "repeats": args.repeats,
        "settings": {
            "ATOMIC_USE_HYBRID_RPC": bool(settings.ATOMIC_USE_HYBRID_RPC),
            "ATOMIC_ENABLE_FTS": bool(settings.ATOMIC_ENABLE_FTS),
            "ATOMIC_ENABLE_GRAPH_HOP": bool(settings.ATOMIC_ENABLE_GRAPH_HOP),
            "ATOMIC_MATCH_THRESHOLD": float(settings.ATOMIC_MATCH_THRESHOLD),
            "ATOMIC_RRF_K": int(settings.ATOMIC_RRF_K),
            "ATOMIC_RRF_VECTOR_WEIGHT": float(settings.ATOMIC_RRF_VECTOR_WEIGHT),
            "ATOMIC_RRF_FTS_WEIGHT": float(settings.ATOMIC_RRF_FTS_WEIGHT),
            "ATOMIC_HNSW_EF_SEARCH": int(settings.ATOMIC_HNSW_EF_SEARCH),
        },
        "retrieval_backend_metrics": retrieval_metrics_store.snapshot(),
        "total_duration_ms": bench_ms,
        "runs": runs,
    }
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
