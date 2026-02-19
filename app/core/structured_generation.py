"""
Structured Generation Engine - Constrained Decoding for LLM Outputs.

This module provides the StrictEngine class that guarantees LLM outputs
conform to Pydantic schemas using the `instructor` library for constrained
decoding.

Key Features:
- Zero-tolerance for malformed JSON
- Automatic retry with validation
- Generic support for any Pydantic model
- Works with Groq, Gemini, OpenAI, Anthropic APIs
"""

import logging
from typing import TypeVar, Type, Optional, Any, Union, Dict
from pydantic import BaseModel, RootModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.settings import settings
from app.infrastructure.ai.instructor_factory import (
    create_async_instructor_client,
    create_instructor_client,
)

logger = logging.getLogger(__name__)


def _compact_error(err: Exception, limit: int = 320) -> str:
    text = str(err or "").replace("\n", " ").strip()
    lowered = text.lower()
    if "max_tokens length limit" in lowered:
        return "max_tokens_length_limit"
    if "json_validate_failed" in lowered:
        return "json_validate_failed"
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


# Generic type for Pydantic models
T = TypeVar("T", bound=BaseModel)


# StrictEngine implementation


# =============================================================================
# STRICT ENGINE
# =============================================================================


class StrictEngine:
    """
    Structured generation engine with constrained decoding.

    Guarantees that LLM outputs conform to Pydantic schemas.
    Uses the `instructor` library for robust JSON generation.
    """

    def __init__(
        self,
        max_retries: int = 2,
        temperature: float = 0.1,
        client: Optional[Any] = None,
        model: Optional[str] = None,
    ):
        """
        Initialize StrictEngine with optional injected client/model.
        """
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = (
            int(settings.STRICT_ENGINE_MAX_TOKENS)
            if getattr(settings, "STRICT_ENGINE_MAX_TOKENS", None)
            else None
        )

        if client is not None and model is not None:
            self._client = client
            self._model = model
            self._async_client = None
        else:
            # Note: This fallback is for convenience, but DI is preferred.
            self._client, self._model = create_instructor_client()
            try:
                self._async_client, _ = create_async_instructor_client()
            except Exception:
                self._async_client = None

    async def agenerate(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
        logit_bias: Optional[Dict[int, int]] = None,
    ) -> T:
        """Async version of generate."""
        if not self._async_client:
            logger.warning("No async client available, falling back to synchronous execution.")
            return self.generate(prompt, schema, system_prompt, context, logit_bias)

        messages = self._build_messages(prompt, system_prompt, context)
        return await self._agenerate_with_retry(messages, schema, logit_bias)

    def generate(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
        logit_bias: Optional[Dict[int, int]] = None,
    ) -> T:
        """
        Generate a structured response conforming to the given schema.

        Args:
            prompt: User prompt describing what to generate.
            schema: Pydantic model class defining the expected output.
            system_prompt: Optional system message for the LLM.
            context: Optional additional context (e.g., rubric text).

        Returns:
            Validated instance of the schema class.

        Raises:
            ValidationError: If output cannot be validated after retries.
            ValueError: If LLM provider fails.
        """
        messages = self._build_messages(prompt, system_prompt, context)

        # Attempt generation with retries
        return self._generate_with_retry(messages, schema, logit_bias)

    def _build_messages(
        self, prompt: str, system_prompt: Optional[str] = None, context: Optional[str] = None
    ) -> list:
        """Shared message building logic."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente experto que genera respuestas estructuradas. "
                        "Tu respuesta DEBE ser un objeto JSON válido que cumpla exactamente "
                        "con el esquema proporcionado. NO incluyas texto antes o después del JSON."
                    ),
                }
            )

        user_content = prompt
        if context:
            user_content = f"CONTEXTO:\n{context}\n\n---\n\nSOLICITUD:\n{prompt}"

        messages.append({"role": "user", "content": user_content})
        return messages

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True
    )
    def _generate_with_retry(
        self,
        messages: list,
        schema: Type[T],
        logit_bias: Optional[Dict[int, int]] = None,
    ) -> T:
        """Internal method with retry logic."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_model=schema,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_retries=self.max_retries,  # instructor's built-in retry
                extra_body={"logit_bias": logit_bias} if logit_bias else {},
            )

            logger.debug(f"StrictEngine generated: {type(response).__name__}")
            return response

        except Exception as e:
            logger.error("StrictEngine generation failed: %s", _compact_error(e))
            raise

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True
    )
    async def _agenerate_with_retry(
        self,
        messages: list,
        schema: Type[T],
        logit_bias: Optional[Dict[int, int]] = None,
    ) -> T:
        """Internal method with retry logic (ASYNC)."""
        try:
            response = await self._async_client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_model=schema,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_retries=self.max_retries,
                extra_body={"logit_bias": logit_bias} if logit_bias else {},
            )
            return response
        except Exception as e:
            logger.error("StrictEngine (Async) failed: %s", _compact_error(e))
            raise

    def generate_or_error(
        self,
        prompt: str,
        success_schema: Type[T],
        error_schema: Type[BaseModel],
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> Union[T, BaseModel]:
        """
        Generate a response that can be either success or error.
        """

        # Create a union type dynamically
        class UnionResponse(RootModel):
            root: Union[success_schema, error_schema]  # type: ignore

        result = self.generate(
            prompt=prompt,
            schema=UnionResponse,
            system_prompt=system_prompt,
            context=context,
        )

        return result.root


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_default_engine: Optional[StrictEngine] = None


def get_strict_engine() -> StrictEngine:
    """Get or create a singleton StrictEngine instance."""
    global _default_engine
    if _default_engine is None:
        _default_engine = StrictEngine()
    return _default_engine


def structured_generate(prompt: str, schema: Type[T], **kwargs) -> T:
    """
    Convenience function for one-off structured generation.

    Example:
        from app.core.structured_generation import structured_generate

        class Response(BaseModel):
            answer: str
            confidence: float

        result = structured_generate("What is 2+2?", Response)
    """
    engine = get_strict_engine()
    return engine.generate(prompt, schema, **kwargs)
