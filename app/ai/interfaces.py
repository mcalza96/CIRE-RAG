"""Provider-agnostic interfaces for structured VLM generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class BaseVLM(ABC):
    """Contract that every VLM adapter must implement."""

    @abstractmethod
    def generate_structured_output(
        self,
        image_content: bytes | str,
        prompt: str,
        schema: type[BaseModel] | dict[str, Any],
        mime_type: str = "image/png",
    ) -> BaseModel | dict[str, Any]:
        """Generate normalized structured data from an image + prompt."""


class ModelAdapterError(RuntimeError):
    """Base exception for adapter failures."""
