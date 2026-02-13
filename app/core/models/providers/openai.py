"""OpenAI adapter implementing the provider-agnostic VLM interface."""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel

from app.core.models.interfaces import BaseVLM, ModelAdapterError


class OpenAIAdapter(BaseVLM):
    """OpenAI adapter for visual structured extraction with GPT-4o family."""

    def __init__(self, model_name: str, api_key: str, temperature: float = 0.0) -> None:
        """Initialize the OpenAI adapter and API client."""

        if not api_key:
            raise ValueError("OpenAIAdapter requires a non-empty API key.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("openai is required for OpenAIAdapter.") from exc

        self._client = OpenAI(api_key=api_key)
        self._model_name = model_name
        self._temperature = temperature

    def generate_structured_output(
        self,
        image_content: bytes | str,
        prompt: str,
        schema: type[BaseModel] | dict[str, Any],
        mime_type: str = "image/png",
    ) -> BaseModel | dict[str, Any]:
        """Generate structured output normalized to `dict` or Pydantic model."""

        image_url = self._build_data_uri(image_content=image_content, mime_type=mime_type)
        model_prompt = self._build_schema_prompt(prompt=prompt, schema=schema)

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                temperature=self._temperature,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": model_prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
            )

            message = response.choices[0].message
            if not message.content:
                raise ModelAdapterError("OpenAI returned an empty response payload.")

            payload = json.loads(message.content)
        except ModelAdapterError:
            raise
        except Exception as exc:
            raise ModelAdapterError(f"OpenAIAdapter request failed: {exc}") from exc

        return self._normalize_output(payload=payload, schema=schema)

    @staticmethod
    def _build_data_uri(image_content: bytes | str, mime_type: str) -> str:
        """Normalize bytes/base64 image input into OpenAI data URI format."""

        if isinstance(image_content, bytes):
            encoded = base64.b64encode(image_content).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        if image_content.startswith("data:"):
            return image_content

        return f"data:{mime_type};base64,{image_content}"

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
