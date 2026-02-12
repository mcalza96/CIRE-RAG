"""Model configuration for provider-agnostic AI adapters."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[5]
ROOT_ENV = PROJECT_ROOT / ".env"
ROOT_ENV_LOCAL = PROJECT_ROOT / ".env.local"
SERVICE_ENV = PROJECT_ROOT / "python-services" / "rag-ingestion" / ".env"
SERVICE_ENV_LOCAL = PROJECT_ROOT / "python-services" / "rag-ingestion" / ".env.local"


class ProviderName(str, Enum):
    """Supported model providers for ingestion/chat runtimes."""

    GOOGLE = "google"
    OPENAI = "openai"
    LOCAL = "local"


class ModelSettings(BaseSettings):
    """Validated environment-backed settings for model adapters."""

    model_config = SettingsConfigDict(
        env_file=(
            str(ROOT_ENV),
            str(ROOT_ENV_LOCAL),
            str(SERVICE_ENV),
            str(SERVICE_ENV_LOCAL),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Global defaults (backward-compatible keys).
    vlm_provider: ProviderName = Field(default=ProviderName.GOOGLE, alias="VLM_PROVIDER")
    vlm_model_name: str = Field(default="gemini-2.5-flash", alias="VLM_MODEL_NAME")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE", ge=0.0, le=2.0)

    # Ingestion (vision) runtime overrides.
    ingest_vlm_provider: ProviderName | None = Field(default=None, alias="INGEST_VLM_PROVIDER")
    ingest_vlm_model_name: str | None = Field(default=None, alias="INGEST_VLM_MODEL_NAME")
    ingest_vlm_fallback_model_name: str | None = Field(
        default="gemini-2.5-flash",
        alias="INGEST_VLM_FALLBACK_MODEL_NAME",
    )
    ingest_vlm_temperature: float | None = Field(
        default=None,
        alias="INGEST_VLM_TEMPERATURE",
        ge=0.0,
        le=2.0,
    )

    # Chat (reasoning) runtime overrides.
    chat_llm_provider: ProviderName | None = Field(default=None, alias="CHAT_LLM_PROVIDER")
    chat_llm_model_name: str | None = Field(default=None, alias="CHAT_LLM_MODEL_NAME")
    chat_llm_temperature: float | None = Field(
        default=None,
        alias="CHAT_LLM_TEMPERATURE",
        ge=0.0,
        le=2.0,
    )

    # Provider credentials.
    google_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"),
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # Local endpoint placeholders for future vLLM/Ollama support.
    local_base_url: str | None = Field(default=None, alias="LOCAL_MODEL_BASE_URL")
    local_api_key: str | None = Field(default=None, alias="LOCAL_MODEL_API_KEY")

    @model_validator(mode="after")
    def validate_provider_model_compatibility(self) -> "ModelSettings":
        """Reject provider/model mismatches early with actionable errors."""

        self._assert_provider_model(
            provider=self.resolved_ingest_provider,
            model_name=self.resolved_ingest_model_name,
            scope="ingest",
        )
        self._assert_provider_model(
            provider=self.resolved_chat_provider,
            model_name=self.resolved_chat_model_name,
            scope="chat",
        )
        return self

    @staticmethod
    def _assert_provider_model(provider: ProviderName, model_name: str, scope: str) -> None:
        """Validate obvious incompatible provider/model combinations."""

        normalized = (model_name or "").strip().lower()
        if not normalized:
            raise ValueError(f"{scope} model name cannot be empty.")

        if provider == ProviderName.OPENAI and (
            normalized.startswith("gemini")
            or normalized.startswith("models/gemini")
        ):
            raise ValueError(
                f"{scope} model '{model_name}' is incompatible with provider '{provider.value}'. "
                "Use an OpenAI model (for example: gpt-4o-mini)."
            )

        if provider == ProviderName.GOOGLE and (
            normalized.startswith("gpt-")
            or normalized.startswith("o1")
            or normalized.startswith("o3")
        ):
            raise ValueError(
                f"{scope} model '{model_name}' is incompatible with provider '{provider.value}'. "
                "Use a Gemini model (for example: gemini-2.5-flash)."
            )

    @property
    def resolved_ingest_provider(self) -> ProviderName:
        """Return the effective provider for ingestion workflows."""

        return self.ingest_vlm_provider or self.vlm_provider

    @property
    def resolved_ingest_model_name(self) -> str:
        """Return the effective model name for ingestion workflows."""

        return self.ingest_vlm_model_name or self.vlm_model_name

    @property
    def resolved_ingest_fallback_model_name(self) -> str | None:
        """Return optional fallback model for ingestion visual parsing."""

        value = (self.ingest_vlm_fallback_model_name or "").strip()
        if not value:
            return None
        if value == self.resolved_ingest_model_name:
            return None
        return value

    @property
    def resolved_ingest_temperature(self) -> float:
        """Return the effective temperature for ingestion workflows."""

        return self.ingest_vlm_temperature if self.ingest_vlm_temperature is not None else self.llm_temperature

    @property
    def resolved_chat_provider(self) -> ProviderName:
        """Return the effective provider for chat workflows."""

        return self.chat_llm_provider or self.vlm_provider

    @property
    def resolved_chat_model_name(self) -> str:
        """Return the effective model name for chat workflows."""

        return self.chat_llm_model_name or self.vlm_model_name

    @property
    def resolved_chat_temperature(self) -> float:
        """Return the effective temperature for chat workflows."""

        return self.chat_llm_temperature if self.chat_llm_temperature is not None else self.llm_temperature


@lru_cache(maxsize=1)
def get_model_settings() -> ModelSettings:
    """Return a cached model settings singleton."""

    return ModelSettings()
