from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.infrastructure.settings import settings
from app.infrastructure.container import CognitiveContainer
from app.main import app


class _FakeRetrievalTools:
    async def retrieve(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "doc-1",
                "content": "cross-tenant data",
                "similarity": 0.9,
                "metadata": {"tenant_id": "tenant-other"},
            }
        ]

    async def retrieve_summaries(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _FakeContainer:
    retrieval_tools = _FakeRetrievalTools()


def _set_deployed() -> tuple[str, str, bool, str]:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "production"
    settings.ENVIRONMENT = "production"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "topsecret"
    return original


def _restore(original: tuple[str, str, bool, str]) -> None:
    settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET = original


def test_retrieval_requires_service_auth_in_deployed() -> None:
    original = _set_deployed()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/debug/retrieval/chunks",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"query": "q", "tenant_id": "tenant-demo"},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    finally:
        _restore(original)


def test_chat_completions_requires_service_auth_in_deployed() -> None:
    original = _set_deployed()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat/completions",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"message": "q", "tenant_id": "tenant-demo"},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    finally:
        _restore(original)


def test_ingestion_embed_requires_service_auth_in_deployed() -> None:
    original = _set_deployed()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/ingestion/embed",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"texts": ["hola"], "task": "retrieval.passage"},
            )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    finally:
        _restore(original)


def test_auth_bypass_fails_closed_when_env_is_inconsistent() -> None:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "local"
    settings.ENVIRONMENT = "development"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "topsecret"

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/debug/retrieval/chunks",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"query": "q", "tenant_id": "tenant-demo"},
            )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "AUTH_ENV_INCONSISTENT"
    finally:
        _restore(original)


def test_auth_bypass_fails_closed_with_default_secret_in_docker_local() -> None:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "local"
    settings.ENVIRONMENT = "development"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "development-secret"

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/debug/retrieval/chunks",
                headers={"X-Tenant-ID": "tenant-demo"},
                json={"query": "q", "tenant_id": "tenant-demo"},
            )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "AUTH_ENV_INCONSISTENT"
    finally:
        _restore(original)


def test_retrieval_chunks_returns_security_isolation_breach_on_canary_detection(monkeypatch) -> None:
    original = _set_deployed()
    monkeypatch.setattr(CognitiveContainer, "get_instance", classmethod(lambda cls: _FakeContainer()))
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/debug/retrieval/chunks",
                headers={"Authorization": "Bearer topsecret", "X-Tenant-ID": "tenant-demo"},
                json={"query": "q", "tenant_id": "tenant-demo"},
            )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "SECURITY_ISOLATION_BREACH"
    finally:
        _restore(original)


def test_official_retrieval_endpoints_require_service_auth_in_deployed() -> None:
    original = _set_deployed()
    try:
        with TestClient(app) as client:
            calls = [
                (
                    "/api/v1/retrieval/validate-scope",
                    {"query": "q", "tenant_id": "tenant-demo"},
                ),
                (
                    "/api/v1/retrieval/hybrid",
                    {"query": "q", "tenant_id": "tenant-demo"},
                ),
                (
                    "/api/v1/retrieval/multi-query",
                    {
                        "tenant_id": "tenant-demo",
                        "queries": [{"id": "q1", "query": "q"}],
                    },
                ),
                (
                    "/api/v1/retrieval/explain",
                    {"query": "q", "tenant_id": "tenant-demo"},
                ),
            ]

            for path, payload in calls:
                response = client.post(path, headers={"X-Tenant-ID": "tenant-demo"}, json=payload)
                assert response.status_code == 401
                assert response.json()["error"]["code"] == "UNAUTHORIZED"
    finally:
        _restore(original)
