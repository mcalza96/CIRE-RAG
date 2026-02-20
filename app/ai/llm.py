from __future__ import annotations

from typing import Any, Dict, Optional, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel

from app.ai.models import AIModelConfig
from app.infrastructure.observability.forensic import ForensicCallbackHandler
from app.infrastructure.observability.metrics import MetricsCallbackHandler


def _default_temperature_for_capability(capability: str) -> float:
    cap = (capability or "CHAT").strip().upper()
    if cap == "DESIGN":
        return AIModelConfig.DEFAULT_TEMPERATURE_DESIGN
    if cap == "FORENSIC":
        return AIModelConfig.DEFAULT_TEMPERATURE_FORENSIC
    if cap == "ORCHESTRATION":
        return AIModelConfig.DEFAULT_TEMPERATURE_ORCHESTRATION
    if cap == "GENERATION":
        return AIModelConfig.DEFAULT_TEMPERATURE_GENERATION
    if cap == "SUMMARIZATION":
        return AIModelConfig.DEFAULT_TEMPERATURE_SUMMARIZATION
    return AIModelConfig.DEFAULT_TEMPERATURE_CHAT


def _callbacks() -> list[BaseCallbackHandler]:
    # Kept as a helper to keep get_llm() readable.
    return [ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")]


def _build_groq(
    *, capability: str, temperature: float, logit_bias: Dict[int, int] | None
) -> BaseChatModel | None:
    if not AIModelConfig.GROQ_API_KEY:
        return None
    try:
        from langchain_groq import ChatGroq
    except ImportError:
        return None

    model_name = AIModelConfig.get_groq_model_for_capability(capability)
    return ChatGroq(
        model=model_name,
        temperature=temperature,
        api_key=cast(Any, AIModelConfig.GROQ_API_KEY),
        model_kwargs={"logit_bias": logit_bias} if logit_bias else {},
        callbacks=_callbacks(),
    )


def _build_gemini(*, temperature: float) -> BaseChatModel | None:
    if not AIModelConfig.is_gemini_available():
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        return None

    return ChatGoogleGenerativeAI(
        model=AIModelConfig.GEMINI_MODEL_NAME,
        temperature=temperature,
        google_api_key=AIModelConfig.GEMINI_API_KEY,
        convert_system_message_to_human=True,
        callbacks=_callbacks(),
    )


def get_llm(
    temperature: Optional[float] = None,
    capability: str = "CHAT",
    logit_bias: Optional[Dict[int, int]] = None,
    prefer_provider: str = "auto",
) -> BaseChatModel:
    """
    Returns the configured LLM based on environment variables and capability.
    Prioritizes Groq (User Preference) -> Gemini.
    """

    cap = (capability or "CHAT").strip().upper()
    temp = _default_temperature_for_capability(cap) if temperature is None else float(temperature)

    preference = (prefer_provider or "auto").strip().lower()
    if preference not in {"auto", "groq", "gemini"}:
        preference = "auto"

    default_order_by_capability = {
        "ORCHESTRATION": ["groq", "gemini"],
        "GENERATION": ["gemini", "groq"],
    }
    if preference == "auto":
        provider_order = default_order_by_capability.get(cap, ["groq", "gemini"])
    else:
        provider_order = [preference, "gemini" if preference == "groq" else "groq"]

    for provider in provider_order:
        if provider == "groq":
            model = _build_groq(capability=cap, temperature=temp, logit_bias=logit_bias)
        else:
            model = _build_gemini(temperature=temp)
        if model is not None:
            return model

    raise ValueError(
        "No valid AI Provider found. Set GROQ_API_KEY or GEMINI_API_KEY. OpenAI is disabled."
    )
