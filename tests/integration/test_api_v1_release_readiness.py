from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.api.v1.routers import documents as documents_router
from app.api.v1.routers.ingestion import get_ingestion_use_case
from app.main import app
from app.core.settings import settings


class _FakeManualIngestionUseCase:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.background_calls = 0

    async def execute(self, file, metadata):
        self.execute_calls += 1
        return "/tmp/demo.pdf", "demo.pdf", object()

    async def process_background(self, file_path, original_filename, metadata):
        self.background_calls += 1
        return {
            "document_id": "doc-123",
            "queue": {"queue_depth": 1, "max_pending": 500, "estimated_wait_seconds": 30},
        }


def test_documents_create_idempotency_replays_payload() -> None:
    fake_use_case = _FakeManualIngestionUseCase()
    documents_router._reset_idempotency_cache_for_tests()
    app.dependency_overrides[get_ingestion_use_case] = lambda: fake_use_case

    try:
        with TestClient(app) as client:
            data = {"metadata": '{"institution_id":"tenant-demo"}'}
            headers = {"Idempotency-Key": "abc-123", "X-Tenant-ID": "tenant-demo"}

            first = client.post(
                "/api/v1/documents",
                files={"file": ("demo.pdf", io.BytesIO(b"pdf"), "application/pdf")},
                data=data,
                headers=headers,
            )
            second = client.post(
                "/api/v1/documents",
                files={"file": ("demo.pdf", io.BytesIO(b"pdf"), "application/pdf")},
                data=data,
                headers=headers,
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.headers.get("X-Idempotency-Replayed") == "true"
        assert first.json() == second.json()
        assert fake_use_case.execute_calls == 1
        assert fake_use_case.background_calls == 1
    finally:
        app.dependency_overrides.clear()
        documents_router._reset_idempotency_cache_for_tests()


def test_ingestion_routes_do_not_include_deprecation_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/ingestion/queue/status?tenant_id=tenant-demo", headers={"X-Tenant-ID": "tenant-demo"})

    assert "Deprecation" not in response.headers
    assert "Sunset" not in response.headers


def test_tenant_header_required_for_s2s_routes() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/ingestion/queue/status?tenant_id=tenant-demo")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TENANT_HEADER_REQUIRED"


def test_invalid_tenant_header_returns_400() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ingestion/queue/status?tenant_id=tenant-demo",
            headers={"X-Tenant-ID": "tenant demo"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TENANT_HEADER"


def test_tenant_mismatch_returns_400() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/management/collections?tenant_id=tenant-a",
            headers={"X-Tenant-ID": "tenant-b"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TENANT_MISMATCH"


def test_exempt_health_route_does_not_require_tenant_header() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_management_tenants_route_does_not_require_tenant_header() -> None:
    class _FakeUseCase:
        async def list_tenants(self, limit: int = 200):
            return [{"id": "tenant-a", "name": "Tenant A"}]

    app.dependency_overrides[get_ingestion_use_case] = lambda: _FakeUseCase()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/management/tenants")

        assert response.status_code == 200
        assert response.json()["items"] == [{"id": "tenant-a", "name": "Tenant A"}]
    finally:
        app.dependency_overrides.clear()


def test_auth_is_enforced_in_deployed_environment() -> None:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "production"
    settings.ENVIRONMENT = "production"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "topsecret"

    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/management/health", headers={"X-Tenant-ID": "tenant-demo"})
            authorized = client.get(
                "/api/v1/management/health",
                headers={"Authorization": "Bearer topsecret", "X-Tenant-ID": "tenant-demo"},
            )

        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"
        assert authorized.status_code == 200
    finally:
        settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET = original


def test_management_tenants_requires_auth_in_deployed_environment() -> None:
    class _FakeUseCase:
        async def list_tenants(self, limit: int = 200):
            return [{"id": "tenant-a", "name": "Tenant A"}]

    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "production"
    settings.ENVIRONMENT = "production"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "topsecret"
    app.dependency_overrides[get_ingestion_use_case] = lambda: _FakeUseCase()

    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/management/tenants")
            authorized = client.get(
                "/api/v1/management/tenants",
                headers={"Authorization": "Bearer topsecret"},
            )

        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"
        assert authorized.status_code == 200
        assert authorized.json()["items"] == [{"id": "tenant-a", "name": "Tenant A"}]
    finally:
        app.dependency_overrides.clear()
        settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET = original
