"""Provider selection helpers for VLM adapters."""

from __future__ import annotations

from app.core.config.model_config import ModelSettings, ProviderName, get_model_settings
from app.core.models.interfaces import BaseVLM
from app.core.models.providers.gemini import GeminiAdapter
from app.core.models.providers.openai import OpenAIAdapter

def _create_provider_model(
    provider: ProviderName,
    model_name: str,
    temperature: float,
    settings: ModelSettings,
) -> BaseVLM:
    if provider == ProviderName.GOOGLE:
        if not settings.google_api_key:
            raise ValueError("Missing GEMINI_API_KEY/GOOGLE_GENERATIVE_AI_API_KEY for google provider.")
        return GeminiAdapter(model_name=model_name, api_key=settings.google_api_key, temperature=temperature)

    if provider == ProviderName.OPENAI:
        if not settings.openai_api_key:
            raise ValueError("Missing OPENAI_API_KEY for openai provider.")
        return OpenAIAdapter(model_name=model_name, api_key=settings.openai_api_key, temperature=temperature)

    if provider == ProviderName.LOCAL:
        raise NotImplementedError("Local provider selected but no local adapter is implemented yet.")

    raise ValueError(f"Unsupported provider: {provider}")


def create_ingest_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return _create_provider_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=resolved_settings.resolved_ingest_model_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )


def create_chat_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return _create_provider_model(
        provider=resolved_settings.resolved_chat_provider,
        model_name=resolved_settings.resolved_chat_model_name,
        temperature=resolved_settings.resolved_chat_temperature,
        settings=resolved_settings,
    )


def create_ingest_fallback_model(settings: ModelSettings | None = None) -> BaseVLM | None:
    resolved_settings = settings or get_model_settings()
    fallback_name = resolved_settings.resolved_ingest_fallback_model_name
    if not fallback_name:
        return None
    return _create_provider_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=fallback_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )
