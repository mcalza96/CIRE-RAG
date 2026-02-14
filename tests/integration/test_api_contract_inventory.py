from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _route_map() -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        allowed = set(methods) - {"HEAD", "OPTIONS"}
        if not allowed:
            continue
        mapping[path] = mapping.get(path, set()) | allowed
    return mapping


def test_api_contract_inventory_is_exact() -> None:
    routes = _route_map()

    expected = {
        "/health": {"GET"},
        "/api/v1/chat/completions": {"POST"},
        "/api/v1/chat/feedback": {"POST"},
        "/api/v1/documents": {"POST", "GET"},
        "/api/v1/documents/{document_id}/status": {"GET"},
        "/api/v1/documents/{document_id}": {"DELETE"},
        "/api/v1/management/tenants": {"GET"},
        "/api/v1/management/collections": {"GET"},
        "/api/v1/management/queue/status": {"GET"},
        "/api/v1/management/health": {"GET"},
        "/api/v1/management/retrieval/metrics": {"GET"},
        "/api/v1/retrieval/validate-scope": {"POST"},
        "/api/v1/retrieval/hybrid": {"POST"},
        "/api/v1/retrieval/multi-query": {"POST"},
        "/api/v1/retrieval/explain": {"POST"},
        "/api/v1/debug/retrieval/chunks": {"POST"},
        "/api/v1/debug/retrieval/summaries": {"POST"},
        "/api/v1/ingestion/embed": {"POST"},
        "/api/v1/ingestion/ingest": {"POST"},
        "/api/v1/ingestion/institutional": {"POST"},
        "/api/v1/ingestion/documents": {"GET"},
        "/api/v1/ingestion/collections": {"GET"},
        "/api/v1/ingestion/queue/status": {"GET"},
        "/api/v1/ingestion/collections/cleanup": {"POST"},
        "/api/v1/ingestion/retry/{doc_id}": {"POST"},
        "/api/v1/ingestion/batches": {"POST"},
        "/api/v1/ingestion/batches/{batch_id}/files": {"POST"},
        "/api/v1/ingestion/batches/{batch_id}/seal": {"POST"},
        "/api/v1/ingestion/batches/{batch_id}/status": {"GET"},
        "/api/v1/ingestion/batches/{batch_id}/progress": {"GET"},
        "/api/v1/ingestion/batches/{batch_id}/events": {"GET"},
        "/api/v1/ingestion/batches/active": {"GET"},
        "/api/v1/ingestion/batches/{batch_id}/stream": {"GET"},
    }

    for path, methods in expected.items():
        assert path in routes, f"Missing route: {path}"
        assert methods.issubset(routes[path]), f"Route {path} missing methods {methods - routes[path]}"

    assert "/api/v1/retrieval/chunks" not in routes
    assert "/api/v1/retrieval/summaries" not in routes
    assert "/api/v1/knowledge/retrieve" not in routes


def test_non_legacy_routes_do_not_emit_deprecation_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/management/health", headers={"X-Tenant-ID": "tenant-demo"})

    assert response.status_code == 200
    assert "Deprecation" not in response.headers
    assert "Sunset" not in response.headers
