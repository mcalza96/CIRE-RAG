"""
Centralized AI Client Factory.
Builds raw LLM clients, VLM adapters, and Instructor-wrapped clients for structured generation.
"""

from __future__ import annotations
import logging
from typing import Any, Tuple, Optional

from app.ai.config import ModelSettings, ProviderName, get_model_settings
from app.ai.contracts import AIModelConfig, BaseVLM
from app.ai.providers.gemini import GeminiAdapter
from app.ai.providers.openai import OpenAIAdapter

logger = logging.getLogger(__name__)


# =============================================================================
# VLM FACTORY (Raw Adapters)
# =============================================================================

def _create_vlm_provider_model(
    provider: ProviderName,
    model_name: str,
    temperature: float,
    settings: ModelSettings,
) -> BaseVLM:
    if provider == ProviderName.GOOGLE:
        if not settings.google_api_key:
            raise ValueError("Missing GEMINI_API_KEY for google provider.")
        return GeminiAdapter(model_name=model_name, api_key=settings.google_api_key, temperature=temperature)

    if provider == ProviderName.OPENAI:
        if not settings.openai_api_key:
            raise ValueError("Missing OPENAI_API_KEY for openai provider.")
        return OpenAIAdapter(model_name=model_name, api_key=settings.openai_api_key, temperature=temperature)

    raise ValueError(f"Unsupported provider: {provider}")


def create_ingest_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return _create_vlm_provider_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=resolved_settings.resolved_ingest_model_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )


def create_ingest_fallback_model(settings: ModelSettings | None = None) -> BaseVLM | None:
    resolved_settings = settings or get_model_settings()
    fallback_model_name = resolved_settings.resolved_ingest_fallback_model_name
    if not fallback_model_name:
        return None
    return _create_vlm_provider_model(
        provider=resolved_settings.resolved_ingest_provider,
        model_name=fallback_model_name,
        temperature=resolved_settings.resolved_ingest_temperature,
        settings=resolved_settings,
    )


def create_chat_model(settings: ModelSettings | None = None) -> BaseVLM:
    resolved_settings = settings or get_model_settings()
    return _create_vlm_provider_model(
        provider=resolved_settings.resolved_chat_provider,
        model_name=resolved_settings.resolved_chat_model_name,
        temperature=resolved_settings.resolved_chat_temperature,
        settings=resolved_settings,
    )


# =============================================================================
# INSTRUCTOR FACTORY (Structured Clients)
# =============================================================================

def _create_gemini_instructor_client(instructor_module: Any) -> Tuple[Any, str]:
    """Build Gemini instructor client using google.genai SDK."""
    if not AIModelConfig.GEMINI_API_KEY:
        raise ValueError("Missing GEMINI_API_KEY")

    try:
        from google import genai
    except ImportError as exc:
        raise ImportError("google-genai package not installed") from exc

    client = genai.Client(api_key=AIModelConfig.GEMINI_API_KEY)
    factory_fn = getattr(instructor_module, "from_genai", None) or getattr(instructor_module, "from_gemini", None)
    
    if factory_fn is None:
        raise AttributeError("Instructor does not support Gemini (missing from_genai/from_gemini)")

    mode = getattr(instructor_module.Mode, "GEMINI_JSON", instructor_module.Mode.JSON)
    wrapped = factory_fn(client=client, mode=mode)
    return wrapped, AIModelConfig.GEMINI_MODEL_NAME


def create_instructor_client(is_async: bool = False) -> Tuple[Any, str]:
    """
    Unified factory for instructor clients (sync/async).
    Prioritizes Groq -> Gemini -> OpenAI.
    """
    try:
        import instructor
    except ImportError:
        raise ImportError("The 'instructor' library is required for structured generation.")

    # 1. Groq
    if AIModelConfig.GROQ_API_KEY:
        try:
            from groq import Groq, AsyncGroq
            base_client = AsyncGroq(api_key=AIModelConfig.GROQ_API_KEY) if is_async else Groq(api_key=AIModelConfig.GROQ_API_KEY)
            client = instructor.from_groq(base_client, mode=instructor.Mode.JSON)
            return client, AIModelConfig.GROQ_MODEL_FORENSIC
        except ImportError:
            logger.warning("groq package not installed, trying next")

    # 2. Gemini
    if AIModelConfig.GEMINI_API_KEY:
        try:
            # Note: handle async specifically if instructor supports it for GenAI
            return _create_gemini_instructor_client(instructor)
        except Exception as exc:
            logger.warning(f"Gemini client init failed: {exc}")

    # 3. OpenAI
    if AIModelConfig.OPENAI_API_KEY:
        try:
            from openai import OpenAI, AsyncOpenAI
            base_client = AsyncOpenAI(api_key=AIModelConfig.OPENAI_API_KEY) if is_async else OpenAI(api_key=AIModelConfig.OPENAI_API_KEY)
            client = instructor.from_openai(base_client)
            return client, AIModelConfig.OPENAI_FALLBACK_MODEL
        except ImportError:
            logger.warning("openai package not installed")

    raise ValueError("No valid AI provider found for instructor client.")


# Legacy Compat Aliases
def create_async_instructor_client() -> Tuple[Any, str]:
    return create_instructor_client(is_async=True)
