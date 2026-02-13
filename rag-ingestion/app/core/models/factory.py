"""Provider selection helpers for VLM adapters."""

from __future__ import annotations

from typing import Callable

from app.core.config.model_config import ModelSettings, ProviderName, get_model_settings
from app.core.models.interfaces import BaseVLM
from app.core.models.providers.gemini import GeminiAdapter
from app.core.models.providers.openai import OpenAIAdapter

ModelBuilder = Callable[[ModelSettings, str, float], BaseVLM]


def _build_google(settings: ModelSettings, model_name: str, temperature: float) -> BaseVLM:
    if not settings.google_api_key:
        raise ValueError("Missing GEMINI_API_KEY/GOOGLE_GENERATIVE_AI_API_KEY for google provider.")
    return GeminiAdapter(model_name=model_name, api_key=settings.google_api_key, temperature=temperature)


def _build_openai(settings: ModelSettings, model_name: str, temperature: float) -> BaseVLM:
    if not settings.openai_api_key:
        raise ValueError("Missing OPENAI_API_KEY for openai provider.")
    return OpenAIAdapter(model_name=model_name, api_key=settings.openai_api_key, temperature=temperature)


def _build_local(_: ModelSettings, __: str, ___: float) -> BaseVLM:
    raise NotImplementedError(
        "Local provider selected but no local adapter is implemented yet. "
        "Add app/core/models/providers/local.py and wire it in model provider registry."
    )


MODEL_BUILDERS: dict[ProviderName, ModelBuilder] = {
    ProviderName.GOOGLE: _build_google,
    ProviderName.OPENAI: _build_openai,
    ProviderName.LOCAL: _build_local,
}


def create_model(
    provider: ProviderName,
    model_name: str,
    temperature: float,
    settings: ModelSettings | None = None,
) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    builder = MODEL_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unsupported provider: {provider}")
    return builder(resolved_settings, model_name, temperature)


def create_ingest_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return create_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=resolved_settings.resolved_ingest_model_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )


def create_chat_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return create_model(
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
    return create_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=fallback_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )
