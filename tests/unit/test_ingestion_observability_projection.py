from datetime import datetime, timedelta, timezone

from app.application.use_cases.manual_ingestion_use_case import ManualIngestionUseCase


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_observability_projection_progress_eta_stalled_and_cursor():
    now = datetime.now(timezone.utc)
    batch = {
        "status": "processing",
        "created_at": _iso(now - timedelta(seconds=100)),
        "total_files": 4,
    }
    docs = [
        {"id": "d1", "status": "queued"},
        {"id": "d2", "status": "processing", "worker_stage": "GRAPH"},
        {"id": "d3", "status": "completed", "worker_stage": "DONE"},
    ]
    events = [
        {"id": "e2", "created_at": _iso(now - timedelta(seconds=200))},
        {"id": "e1", "created_at": _iso(now - timedelta(seconds=250))},
    ]
    stage_counts = {"GRAPH": 1, "QUEUED": 2}

    out = ManualIngestionUseCase._build_observability_projection(
        batch=batch,
        docs=docs,
        events=events,
        stage_counts=stage_counts,
        queue_snapshot={"estimated_wait_seconds": 15},
    )

    # queued(0.05) + graph(0.88) + terminal(1.0) + missing_doc(0.05) => 1.98 / 4 = 49.5%
    assert out["progress_percent"] == 49.5
    assert out["dominant_stage"] == "QUEUED"
    # Throughput-based ETA should dominate queue fallback (15s).
    assert out["eta_seconds"] >= 1
    assert out["stalled"] is True
    assert out["cursor"] == f"{events[0]['created_at']}|e2"
    assert out["total_files"] == 4
    assert out["terminal_docs"] == 1


def test_observability_projection_uses_queue_eta_when_no_throughput():
    now = datetime.now(timezone.utc)
    out = ManualIngestionUseCase._build_observability_projection(
        batch={"status": "processing", "created_at": _iso(now - timedelta(seconds=60)), "total_files": 2},
        docs=[
            {"id": "d1", "status": "queued"},
            {"id": "d2", "status": "processing", "worker_stage": "INGEST"},
        ],
        events=[],
        stage_counts={"INGEST": 1, "QUEUED": 1},
        queue_snapshot={"estimated_wait_seconds": 77},
    )

    assert out["eta_seconds"] == 77
    assert out["stalled"] is False
