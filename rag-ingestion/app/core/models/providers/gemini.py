"""Gemini adapter implementing the provider-agnostic VLM interface."""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel

from app.core.models.interfaces import BaseVLM, ModelAdapterError


class GeminiAdapter(BaseVLM):
    """Google Generative AI adapter for visual structured extraction."""

    def __init__(self, model_name: str, api_key: str, temperature: float = 0.0) -> None:
        """Initialize the Gemini adapter and underlying model client."""

        if not api_key:
            raise ValueError("GeminiAdapter requires a non-empty API key.")

        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError("google-generativeai is required for GeminiAdapter.") from exc

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model_name=model_name)
        self._temperature = temperature

    def generate_structured_output(
        self,
        image_content: bytes | str,
        prompt: str,
        schema: type[BaseModel] | dict[str, Any],
        mime_type: str = "image/png",
    ) -> BaseModel | dict[str, Any]:
        """Generate structured output normalized to `dict` or Pydantic model."""

        image_part = self._build_image_part(image_content=image_content, mime_type=mime_type)
        model_prompt = self._build_schema_prompt(prompt=prompt, schema=schema)

        try:
            response = self._model.generate_content(
                [model_prompt, image_part],
                generation_config={
                    "temperature": self._temperature,
                    "response_mime_type": "application/json",
                },
            )
            response_text = self._extract_response_text(response)
            if not response_text:
                raise ModelAdapterError("Gemini returned an empty response payload.")

            payload = json.loads(response_text)
        except ModelAdapterError:
            raise
        except Exception as exc:
            raise ModelAdapterError(f"GeminiAdapter request failed: {exc}") from exc

        return self._normalize_output(payload=payload, schema=schema)

    @staticmethod
    def _build_image_part(image_content: bytes | str, mime_type: str) -> dict[str, Any]:
        """Build a Gemini-compatible inline image payload."""

        if isinstance(image_content, bytes):
            encoded = base64.b64encode(image_content).decode("ascii")
            return {"mime_type": mime_type, "data": encoded}

        if image_content.startswith("data:"):
            _, encoded = image_content.split(",", 1)
            return {"mime_type": mime_type, "data": encoded}

        return {"mime_type": mime_type, "data": image_content}

    @staticmethod
    def _build_schema_prompt(prompt: str, schema: type[BaseModel] | dict[str, Any]) -> str:
        """Augment user prompt with JSON schema instructions."""

        schema_json = schema.model_json_schema() if isinstance(schema, type) else schema
        schema_block = json.dumps(schema_json, ensure_ascii=True)
        return (
            f"{prompt}\n\n"
            "Return only valid JSON following this schema exactly:\n"
            f"{schema_block}"
        )

    @staticmethod
    def _normalize_output(
        payload: dict[str, Any],
        schema: type[BaseModel] | dict[str, Any],
    ) -> BaseModel | dict[str, Any]:
        """Normalize provider payload into stable adapter output."""

        if isinstance(schema, type):
            return schema.model_validate(payload)
        return payload

    @staticmethod
    def _extract_finish_reason(response: Any) -> str:
        try:
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                return ""
            reason = getattr(candidates[0], "finish_reason", None)
            if reason is None:
                return ""
            name = getattr(reason, "name", None)
            if isinstance(name, str) and name:
                return name
            return str(reason)
        except Exception:
            return ""

    @classmethod
    def _extract_response_text(cls, response: Any) -> str | None:
        try:
            text = getattr(response, "text", None)
        except Exception as exc:
            finish_reason = cls._extract_finish_reason(response)
            details = str(exc)
            signal = f"{finish_reason} {details}".lower()
            if "finish_reason" in signal and ("4" in signal or "recitation" in signal):
                raise ModelAdapterError(
                    f"GEMINI_COPYRIGHT_BLOCK finish_reason={finish_reason or '4'}: {details}"
                ) from exc
            raise

        if text:
            return text

        finish_reason = cls._extract_finish_reason(response)
        if finish_reason and (finish_reason == "4" or "RECITATION" in finish_reason.upper()):
            raise ModelAdapterError(f"GEMINI_COPYRIGHT_BLOCK finish_reason={finish_reason}")
        return text
