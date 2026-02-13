"""Validation tests for provider/model compatibility in model settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config.model_config import ModelSettings, ProviderName


def test_openai_provider_rejects_gemini_model_name() -> None:
    """OpenAI provider must not accept Gemini model names."""

    with pytest.raises(ValidationError):
        ModelSettings.model_validate(
            {
                "VLM_PROVIDER": "openai",
                "VLM_MODEL_NAME": "gemini-2.5-flash-lite",
                "OPENAI_API_KEY": "test-key",
            }
        )


def test_google_provider_rejects_gpt_model_name() -> None:
    """Google provider must not accept GPT/O-series model names."""

    with pytest.raises(ValidationError):
        ModelSettings.model_validate(
            {
                "VLM_PROVIDER": "google",
                "VLM_MODEL_NAME": "gpt-4o-mini",
                "GEMINI_API_KEY": "test-key",
            }
        )


def test_ingest_override_precedence_is_explicit() -> None:
    """INGEST_VLM_* overrides global VLM_* only for ingestion."""

    settings = ModelSettings.model_validate(
        {
            "VLM_PROVIDER": "google",
            "VLM_MODEL_NAME": "gemini-2.5-flash-lite",
            "INGEST_VLM_PROVIDER": "openai",
            "INGEST_VLM_MODEL_NAME": "gpt-4o-mini",
            "OPENAI_API_KEY": "test-openai",
            "GEMINI_API_KEY": "test-gemini",
        }
    )

    assert settings.resolved_ingest_provider == ProviderName.OPENAI
    assert settings.resolved_ingest_model_name == "gpt-4o-mini"
    assert settings.resolved_chat_provider == ProviderName.GOOGLE
    assert settings.resolved_chat_model_name == "gemini-2.5-flash-lite"
