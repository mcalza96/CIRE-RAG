"""
Centralized AI Generation Engine.
Consolidates llm.py (LangChain clients) and structured_generation.py (StrictEngine/Instructor).
"""

from __future__ import annotations
import logging
import time
import json
import asyncio
from typing import Any, Dict, List, Optional, Type, TypeVar, Union, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, RootModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.ai.contracts import AIModelConfig
from app.ai.factory import create_instructor_client, create_async_instructor_client
from app.infrastructure.settings import settings
from app.infrastructure.observability.forensic import ForensicCallbackHandler
from app.infrastructure.observability.metrics import MetricsCallbackHandler

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


# =============================================================================
# LANGCHAIN LLM BRIDGES
# =============================================================================

def _default_temperature_for_capability(capability: str) -> float:
    cap = (capability or "CHAT").strip().upper()
    capability_map = {
        "DESIGN": AIModelConfig.DEFAULT_TEMPERATURE_DESIGN,
        "FORENSIC": AIModelConfig.DEFAULT_TEMPERATURE_FORENSIC,
        "ORCHESTRATION": AIModelConfig.DEFAULT_TEMPERATURE_ORCHESTRATION,
        "GENERATION": AIModelConfig.DEFAULT_TEMPERATURE_GENERATION,
        "SUMMARIZATION": AIModelConfig.DEFAULT_TEMPERATURE_SUMMARIZATION,
    }
    return capability_map.get(cap, AIModelConfig.DEFAULT_TEMPERATURE_CHAT)


def _callbacks() -> list[BaseCallbackHandler]:
    return [ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")]


def _build_groq(*, capability: str, temperature: float, logit_bias: Dict[int, int] | None) -> BaseChatModel | None:
    if not AIModelConfig.GROQ_API_KEY: return None
    try:
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=AIModelConfig.get_groq_model_for_capability(capability),
            temperature=temperature,
            api_key=cast(Any, AIModelConfig.GROQ_API_KEY),
            model_kwargs={"logit_bias": logit_bias} if logit_bias else {},
            callbacks=_callbacks(),
        )
    except ImportError: return None


def _build_gemini(*, temperature: float) -> BaseChatModel | None:
    if not AIModelConfig.is_gemini_available(): return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=AIModelConfig.GEMINI_MODEL_NAME,
            temperature=temperature,
            google_api_key=AIModelConfig.GEMINI_API_KEY,
            convert_system_message_to_human=True,
            callbacks=_callbacks(),
        )
    except ImportError: return None


def get_llm(
    temperature: Optional[float] = None,
    capability: str = "CHAT",
    logit_bias: Optional[Dict[int, int]] = None,
    prefer_provider: str = "auto",
) -> BaseChatModel:
    """Returns the configured LLM based on capability and preference."""
    cap = (capability or "CHAT").strip().upper()
    temp = _default_temperature_for_capability(cap) if temperature is None else float(temperature)
    preference = (prefer_provider or "auto").strip().lower()

    order = {"ORCHESTRATION": ["groq", "gemini"], "GENERATION": ["gemini", "groq"]}.get(cap, ["groq", "gemini"])
    if preference != "auto":
        order = [preference, "gemini" if preference == "groq" else "groq"]

    for provider in order:
        model = _build_groq(capability=cap, temperature=temp, logit_bias=logit_bias) if provider == "groq" else _build_gemini(temperature=temp)
        if model: return model

    raise ValueError("No valid AI Provider found (GROQ_API_KEY or GEMINI_API_KEY required).")


# =============================================================================
# STRUCTURED GENERATION (StrictEngine)
# =============================================================================

def _compact_error(err: Exception, limit: int = 320) -> str:
    text = str(err or "").replace("\n", " ").strip()
    return text[:limit] + "..." if len(text) > limit else text


class StrictEngine:
    """Guarantees that LLM outputs conform to Pydantic schemas using instructor."""

    def __init__(self, max_retries: int = 2, temperature: float = 0.1, client: Optional[Any] = None, model: Optional[str] = None):
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = int(settings.STRICT_ENGINE_MAX_TOKENS) if getattr(settings, "STRICT_ENGINE_MAX_TOKENS", None) else None

        if client and model:
            self._client, self._model, self._async_client = client, model, None
        else:
            self._client, self._model = create_instructor_client()
            try:
                self._async_client, _ = create_async_instructor_client()
            except Exception:
                self._async_client = None

    async def agenerate(self, prompt: str, schema: Type[T], system_prompt: Optional[str] = None, context: Optional[str] = None, logit_bias: Optional[Dict[int, int]] = None) -> T:
        if not self._async_client: return self.generate(prompt, schema, system_prompt, context, logit_bias)
        messages = self._build_messages(prompt, system_prompt, context)
        return await self._agenerate_with_retry(messages, schema, logit_bias)

    def generate(self, prompt: str, schema: Type[T], system_prompt: Optional[str] = None, context: Optional[str] = None, logit_bias: Optional[Dict[int, int]] = None) -> T:
        messages = self._build_messages(prompt, system_prompt, context)
        return self._generate_with_retry(messages, schema, logit_bias)

    def _build_messages(self, prompt: str, system_prompt: Optional[str] = None, context: Optional[str] = None) -> list:
        sys = system_prompt or "Eres un asistente experto que genera respuestas estructuradas en JSON."
        user = f"CONTEXTO:\n{context}\n\n---\n\nSOLICITUD:\n{prompt}" if context else prompt
        return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    def _generate_with_retry(self, messages: list, schema: Type[T], logit_bias: Optional[Dict[int, int]] = None) -> T:
        try:
            return self._client.chat.completions.create(
                model=self._model, messages=messages, response_model=schema,
                temperature=self.temperature, max_tokens=self.max_tokens,
                max_retries=self.max_retries, extra_body={"logit_bias": logit_bias} if logit_bias else {},
            )
        except Exception as e:
            logger.error("StrictEngine failed: %s", _compact_error(e)); raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def _agenerate_with_retry(self, messages: list, schema: Type[T], logit_bias: Optional[Dict[int, int]] = None) -> T:
        try:
            return await self._async_client.chat.completions.create(
                model=self._model, messages=messages, response_model=schema,
                temperature=self.temperature, max_tokens=self.max_tokens,
                max_retries=self.max_retries, extra_body={"logit_bias": logit_bias} if logit_bias else {},
            )
        except Exception as e:
            logger.error("StrictEngine (Async) failed: %s", _compact_error(e)); raise

    _union_cache: Dict[tuple, Type[RootModel]] = {}

    def generate_or_error(self, prompt: str, success_schema: Type[T], error_schema: Type[BaseModel], **kwargs) -> Union[T, BaseModel]:
        key = (success_schema, error_schema)
        if key not in self._union_cache:
            class UnionResp(RootModel): root: Union[success_schema, error_schema] # type: ignore
            self._union_cache[key] = UnionResp
        return self.generate(prompt=prompt, schema=self._union_cache[key], **kwargs).root


_default_engine: Optional[StrictEngine] = None

def get_strict_engine() -> StrictEngine:
    global _default_engine
    if _default_engine is None: _default_engine = StrictEngine()
    return _default_engine

def structured_generate(prompt: str, schema: Type[T], **kwargs) -> T:
    return get_strict_engine().generate(prompt, schema, **kwargs)
