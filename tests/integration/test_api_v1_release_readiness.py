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
            headers = {"Idempotency-Key": "abc-123"}

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


def test_legacy_routes_include_deprecation_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/ingestion/queue/status")

    assert response.headers.get("Deprecation") == "true"
    assert response.headers.get("Sunset") is not None


def test_auth_is_enforced_in_deployed_environment() -> None:
    original = (settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET)
    settings.APP_ENV = "production"
    settings.ENVIRONMENT = "production"
    settings.RUNNING_IN_DOCKER = True
    settings.RAG_SERVICE_SECRET = "topsecret"

    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/management/health")
            authorized = client.get("/api/v1/management/health", headers={"Authorization": "Bearer topsecret"})

        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"
        assert authorized.status_code == 200
    finally:
        settings.APP_ENV, settings.ENVIRONMENT, settings.RUNNING_IN_DOCKER, settings.RAG_SERVICE_SECRET = original
