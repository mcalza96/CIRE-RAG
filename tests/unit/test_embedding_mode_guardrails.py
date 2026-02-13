from __future__ import annotations

import pytest

from app.core.settings import Settings
from app.services import embedding_service


class _DummyCloudProvider:
    async def embed(self, texts, task="retrieval.passage"):
        return [[0.0] for _ in texts]

    async def chunk_and_encode(self, text):
        return []


class _DummySettings:
    def __init__(self, *, app_env: str, jina_mode: str, jina_api_key: str | None):
        self.APP_ENV = app_env
        self.ENVIRONMENT = app_env
        self.JINA_MODE = jina_mode
        self.JINA_API_KEY = jina_api_key
        self.EMBEDDING_CONCURRENCY = 1
        self.RUNNING_IN_DOCKER = app_env != "local"
        self.is_deployed_environment = app_env in {"staging", "production"}


def test_settings_force_cloud_when_local_mode_in_production() -> None:
    settings = Settings.model_validate({"APP_ENV": "production", "JINA_MODE": "LOCAL"})

    assert settings.is_deployed_environment is True
    assert settings.JINA_MODE == "CLOUD"


def test_settings_allow_local_mode_when_app_env_local() -> None:
    settings = Settings.model_validate({"APP_ENV": "local", "JINA_MODE": "LOCAL"})

    assert settings.is_deployed_environment is False
    assert settings.JINA_MODE == "LOCAL"


def test_embedding_service_blocks_local_override_in_deployed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = _DummyCloudProvider()
    monkeypatch.setattr(embedding_service, "settings", _DummySettings(app_env="production", jina_mode="LOCAL", jina_api_key="x"))
    monkeypatch.setattr(embedding_service, "JinaCloudProvider", lambda api_key: cloud)
    monkeypatch.setattr(embedding_service.JinaEmbeddingService, "_instance", None)

    service = embedding_service.JinaEmbeddingService.get_instance()

    assert service.default_mode == "CLOUD"
    assert service._get_provider("LOCAL") is cloud


def test_embedding_service_requires_cloud_key_in_deployed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_service, "settings", _DummySettings(app_env="production", jina_mode="CLOUD", jina_api_key=None))
    monkeypatch.setattr(embedding_service.JinaEmbeddingService, "_instance", None)

    with pytest.raises(RuntimeError, match="requires JINA_MODE=CLOUD"):
        embedding_service.JinaEmbeddingService.get_instance()
