"""Factory for provider-agnostic model adapter creation."""

from __future__ import annotations

from app.core.config.model_config import ModelSettings, ProviderName, get_model_settings
from app.core.models.interfaces import BaseVLM
from app.core.models.providers.gemini import GeminiAdapter
from app.core.models.providers.openai import OpenAIAdapter


class ModelFactory:
    """Create concrete adapters from validated configuration."""

    def __init__(self, settings: ModelSettings | None = None) -> None:
        """Initialize the factory with dependency-injected settings."""

        self._settings = settings or get_model_settings()

    def create_ingest_model(self) -> BaseVLM:
        """Create the ingestion-time visual model adapter."""

        return self._create_model(
            provider=self._settings.resolved_ingest_provider,
            model_name=self._settings.resolved_ingest_model_name,
            temperature=self._settings.resolved_ingest_temperature,
        )

    def create_chat_model(self) -> BaseVLM:
        """Create the chat-time reasoning model adapter."""

        return self._create_model(
            provider=self._settings.resolved_chat_provider,
            model_name=self._settings.resolved_chat_model_name,
            temperature=self._settings.resolved_chat_temperature,
        )

    def create_ingest_fallback_model(self) -> BaseVLM | None:
        """Create optional ingestion fallback model adapter."""

        fallback_name = self._settings.resolved_ingest_fallback_model_name
        if not fallback_name:
            return None

        return self._create_model(
            provider=self._settings.resolved_ingest_provider,
            model_name=fallback_name,
            temperature=self._settings.resolved_ingest_temperature,
        )

    def _create_model(self, provider: ProviderName, model_name: str, temperature: float) -> BaseVLM:
        """Build the concrete adapter for the requested provider."""

        if provider == ProviderName.GOOGLE:
            if not self._settings.google_api_key:
                raise ValueError("Missing GEMINI_API_KEY/GOOGLE_GENERATIVE_AI_API_KEY for google provider.")
            return GeminiAdapter(model_name=model_name, api_key=self._settings.google_api_key, temperature=temperature)

        if provider == ProviderName.OPENAI:
            if not self._settings.openai_api_key:
                raise ValueError("Missing OPENAI_API_KEY for openai provider.")
            return OpenAIAdapter(model_name=model_name, api_key=self._settings.openai_api_key, temperature=temperature)

        if provider == ProviderName.LOCAL:
            raise NotImplementedError(
                "Local provider selected but no local adapter is implemented yet. "
                "Add app/core/models/providers/local.py and wire it in ModelFactory."
            )

        raise ValueError(f"Unsupported provider: {provider}")


def create_ingest_model(settings: ModelSettings | None = None) -> BaseVLM:
    """Convenience function for DI containers and direct usage."""

    return ModelFactory(settings=settings).create_ingest_model()


def create_chat_model(settings: ModelSettings | None = None) -> BaseVLM:
    """Convenience function for DI containers and direct usage."""

    return ModelFactory(settings=settings).create_chat_model()
