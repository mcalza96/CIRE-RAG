from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.infrastructure.settings import settings
from app.infrastructure.container import CognitiveContainer
from app.main import app


class _FakeRetrievalTools:
    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior

    async def retrieve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        query = str(kwargs.get("query") or "")
        tenant_id = str((kwargs.get("scope_context") or {}).get("tenant_id") or "tenant-demo")

        if self.behavior == "all_fail":
            raise RuntimeError("upstream down")
        if self.behavior == "all_empty":
            return {
                "items": [],
                "trace": {
                    "filters_applied": {"tenant_id": tenant_id},
                    "engine_mode": "atomic",
                    "planner_used": False,
                    "planner_multihop": False,
                    "fallback_used": False,
                    "timings_ms": {"total": 7.5},
                },
            }
        if self.behavior == "partial_fail" and query.lower() == "fail":
            raise RuntimeError("subquery failed")
        if self.behavior == "leak":
            tenant_id = "tenant-other"

        return {
            "items": [
                {
                    "id": f"doc-{query or 'q'}",
                    "content": f"evidence for {query or 'q'}",
                    "similarity": 0.91,
                    "score": 0.91,
                    "source_layer": "vector",
                    "source_type": "content_chunk",
                    "collection_id": "col-1",
                    "created_at": "2026-01-15T00:00:00+00:00",
                    "metadata": {
                        "tenant_id": tenant_id,
                        "department": "qa",
                    },
                }
            ],
            "trace": {
                "filters_applied": {"tenant_id": tenant_id},
                "engine_mode": "atomic",
                "planner_used": False,
                "planner_multihop": False,
                "fallback_used": False,
                "timings_ms": {"total": 7.5},
            },
        }

    async def retrieve_summaries(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _FakeContainer:
    def __init__(self, behavior: str = "ok") -> None:
        self.retrieval_tools = _FakeRetrievalTools(behavior=behavior)


def _set_local() -> tuple[str, str, bool, str]:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "local"
    settings.ENVIRONMENT = "development"
    settings.RUNNING_IN_DOCKER = False
    settings.RAG_SERVICE_SECRET = "development-secret"
    return original


def _restore(original: tuple[str, str, bool, str]) -> None:
    settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET = original


def test_hybrid_endpoint_returns_items_and_trace(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("ok")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/hybrid",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"query": "iso 9001", "tenant_id": "tenant-demo", "k": 5},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["items"]
        assert payload["trace"]["engine_mode"] == "atomic"
        assert "timings_ms" in payload["trace"]
    finally:
        _restore(original)


def test_multi_query_partial_returns_200(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("partial_fail")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/multi-query",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={
                    "tenant_id": "tenant-demo",
                    "queries": [
                        {"id": "q1", "query": "ok"},
                        {"id": "q2", "query": "fail"},
                    ],
                    "merge": {"strategy": "rrf", "rrf_k": 60, "top_k": 5},
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["partial"] is True
        assert any(sq["status"] == "error" for sq in payload["subqueries"])
        assert payload["items"]
    finally:
        _restore(original)


def test_multi_query_all_failed_returns_502(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("all_fail")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/multi-query",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={
                    "tenant_id": "tenant-demo",
                    "queries": [
                        {"id": "q1", "query": "fail-1"},
                        {"id": "q2", "query": "fail-2"},
                    ],
                },
            )

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "MULTI_QUERY_ALL_FAILED"
    finally:
        _restore(original)


def test_multi_query_all_empty_returns_200(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("all_empty")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/multi-query",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={
                    "tenant_id": "tenant-demo",
                    "queries": [
                        {"id": "q1", "query": "empty-1"},
                        {"id": "q2", "query": "empty-2"},
                    ],
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["items"] == []
        assert payload["partial"] is False
        assert all(sq["status"] == "ok" for sq in payload["subqueries"])
    finally:
        _restore(original)


def test_explain_endpoint_returns_score_components(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("ok")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/explain",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={
                    "query": "iso 9001",
                    "tenant_id": "tenant-demo",
                    "collection_id": "col-1",
                    "top_n": 1,
                    "filters": {
                        "metadata": {"department": "qa"},
                        "time_range": {
                            "field": "created_at",
                            "from": "2026-01-01T00:00:00Z",
                            "to": "2026-02-01T00:00:00Z",
                        },
                    },
                },
            )

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["explain"]["score_components"]["final_score"] > 0
        assert item["explain"]["retrieval_path"]["source_layer"] == "vector"
    finally:
        _restore(original)


def test_validate_scope_rejects_prohibited_filters() -> None:
    original = _set_local()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/validate-scope",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={
                    "query": "iso 9001",
                    "tenant_id": "tenant-demo",
                    "filters": {"metadata": {"tenant_id": "tenant-evil"}},
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["valid"] is False
        assert any(v["code"] == "INVALID_SCOPE_FILTER" for v in payload["violations"])
    finally:
        _restore(original)


def test_hybrid_returns_tenant_mismatch_when_header_differs() -> None:
    original = _set_local()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/hybrid",
                headers={"X-Tenant-ID": "tenant-header"},
                json={"query": "iso 9001", "tenant_id": "tenant-body"},
            )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "TENANT_MISMATCH"
    finally:
        _restore(original)


def test_hybrid_returns_security_isolation_breach_when_canary_detects_leak(monkeypatch) -> None:
    original = _set_local()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer("leak")))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/retrieval/hybrid",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"query": "iso 9001", "tenant_id": "tenant-demo"},
            )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "SECURITY_ISOLATION_BREACH"
    finally:
        _restore(original)
