"""Async service that parses visual nodes through provider-agnostic VLM adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from app.core.caching.middleware import cached_extraction
from app.core.models.factory import ModelFactory
from app.core.models.interfaces import BaseVLM, ModelAdapterError
from app.core.models.schemas import VerificationResult, VisualParseResult
from app.core.settings import settings
from app.services.ingestion.prompts import (
    FORENSIC_VISUAL_SYSTEM_PROMPT,
    build_visual_parse_user_prompt,
)
from app.services.ingestion.verify_extraction import ExtractionVerifier

logger = structlog.get_logger(__name__)


class VisualParsingError(RuntimeError):
    """Raised when a visual parse request cannot be completed deterministically."""


class VisualDocumentParser:
    """Provider-agnostic orchestrator for visual document parsing."""

    def __init__(
        self,
        model_factory: ModelFactory | None = None,
        model: BaseVLM | None = None,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.35,
        request_timeout_seconds: float | None = None,
    ) -> None:
        """Initialize parser with dependency-injected model factory/adapter."""

        self._factory = model_factory or ModelFactory()
        self._model = model
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._request_timeout_seconds = (
            float(request_timeout_seconds)
            if request_timeout_seconds is not None
            else float(getattr(settings, "VISUAL_PARSE_TIMEOUT_SECONDS", 45))
        )
        self._heart_enabled = settings.ENABLE_HEART_VERIFICATION
        self._verifier = ExtractionVerifier() if self._heart_enabled else None

    @staticmethod
    def _is_non_retryable_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "gemini_copyright_block" in text
            or ("finish_reason" in text and "4" in text)
            or "copyright" in text
            or "reciting from copyrighted material" in text
        )

    @staticmethod
    def _is_technical_parse_error(exc: Exception) -> bool:
        text = str(exc).lower()
        if isinstance(exc, asyncio.TimeoutError):
            return True
        return (
            "unterminated string" in text
            or "expecting value" in text
            or "invalid control character" in text
            or "empty response payload" in text
            or "json" in text
        )

    async def parse_image(
        self,
        image_path: str | Path | None = None,
        image_bytes: bytes | None = None,
        content_type: str = "table",
        source_metadata: dict[str, Any] | None = None,
        mime_type: str = "image/png",
        token_usage: dict[str, Any] | None = None,
    ) -> VisualParseResult:
        """Parse a visual asset into deterministic structured output."""

        return await self._parse_image_uncached(
            image_path=image_path,
            image_bytes=image_bytes,
            content_type=content_type,
            source_metadata=source_metadata,
            mime_type=mime_type,
            token_usage=token_usage,
        )

    @cached_extraction
    async def _parse_image_uncached(
        self,
        image_path: str | Path | None = None,
        image_bytes: bytes | None = None,
        content_type: str = "table",
        source_metadata: dict[str, Any] | None = None,
        mime_type: str = "image/png",
        token_usage: dict[str, Any] | None = None,
    ) -> VisualParseResult:
        """Internal parse execution path that always calls the VLM provider."""

        _ = token_usage

        image_payload = await self._resolve_image_payload(image_path=image_path, image_bytes=image_bytes)
        user_prompt = self._build_prompt(content_type=content_type, source_metadata=source_metadata)
        model = self._model or self._factory.create_ingest_model()
        model_escalated = False

        last_error: Exception | None = None
        total_attempts = self._max_retries + 1
        attempts_done = 0

        for attempt in range(1, total_attempts + 1):
            attempts_done = attempt
            try:
                parsed = await asyncio.wait_for(
                    asyncio.to_thread(
                        model.generate_structured_output,
                        image_payload,
                        user_prompt,
                        VisualParseResult,
                        mime_type,
                    ),
                    timeout=self._request_timeout_seconds,
                )

                if isinstance(parsed, dict):
                    parsed = VisualParseResult.model_validate(parsed)

                if not isinstance(parsed, VisualParseResult):
                    raise VisualParsingError(f"Unsupported adapter output type: {type(parsed)!r}")

                # --- HEART Verification ---
                if self._heart_enabled and self._verifier is not None:
                    verification = await self._verifier.verify(
                        image_bytes=image_payload,
                        parse_result=parsed,
                        model=model,
                        mime_type=mime_type,
                    )

                    if not verification.is_valid:
                        logger.warning(
                            "heart_verification_discrepancy",
                            attempt=attempt,
                            discrepancies=verification.discrepancies,
                        )
                        # On last attempt, return with unverified flag rather than fail
                        if attempt >= total_attempts:
                            parsed.visual_metadata["verification_status"] = "unverified"
                            parsed.visual_metadata["heart_discrepancies"] = verification.discrepancies
                            return parsed
                        # Retry with negative feedback
                        feedback = "; ".join(verification.discrepancies)
                        user_prompt = (
                            f"{user_prompt}\n\n"
                            f"CORRECCIÓN REQUERIDA: Un auditor detectó estos errores en tu extracción anterior: {feedback}. "
                            "Corrige los valores y vuelve a extraer con máxima precisión."
                        )
                        await asyncio.sleep(self._retry_delay_seconds)
                        continue
                    else:
                        parsed.visual_metadata["verification_status"] = "verified"

                return parsed
            except (ModelAdapterError, ValidationError, ValueError, VisualParsingError, asyncio.TimeoutError) as exc:
                last_error = exc
                error_text = str(exc)
                if isinstance(exc, asyncio.TimeoutError):
                    error_text = f"visual_parse_timeout>{self._request_timeout_seconds}s"
                logger.warning(
                    "visual_parse_attempt_failed",
                    attempt=attempt,
                    max_attempts=total_attempts,
                    error=error_text,
                )
                if self._is_non_retryable_error(exc):
                    logger.warning(
                        "visual_parse_non_retryable_failure",
                        attempt=attempt,
                        reason="copyright_block",
                        error=str(exc),
                    )
                    break

                if not model_escalated and self._is_technical_parse_error(exc):
                    try:
                        fallback_model = self._factory.create_ingest_fallback_model()
                    except Exception as fallback_exc:
                        fallback_model = None
                        logger.warning(
                            "visual_parse_model_escalation_unavailable",
                            error=str(fallback_exc),
                        )
                    if fallback_model is not None:
                        model = fallback_model
                        model_escalated = True
                        logger.info(
                            "visual_parse_model_escalated",
                            reason="technical_parse_error",
                        )
                        if attempt < total_attempts:
                            continue

                if attempt < total_attempts:
                    await asyncio.sleep(self._retry_delay_seconds)

        raise VisualParsingError(f"Visual parsing failed after {attempts_done} attempts: {last_error}")

    async def parse_task(self, task: Any, mime_type: str = "image/png") -> VisualParseResult:
        """Parse a routed ingestion task that points to a visual image path."""

        image_path = getattr(task, "raw_content", None)
        if not isinstance(image_path, str) or not image_path:
            raise VisualParsingError("Task does not contain a valid visual image path in raw_content.")

        content_type = str(getattr(task, "content_type", "table"))
        metadata = getattr(task, "metadata", None)
        if metadata is not None and not isinstance(metadata, dict):
            metadata = {"task_metadata": str(metadata)}

        return await self.parse_image(
            image_path=image_path,
            content_type=content_type,
            source_metadata=metadata,
            mime_type=mime_type,
        )

    async def _resolve_image_payload(
        self,
        image_path: str | Path | None,
        image_bytes: bytes | None,
    ) -> bytes:
        """Resolve image payload from path or in-memory bytes."""

        if image_bytes is not None:
            if not image_bytes:
                raise VisualParsingError("image_bytes is empty.")
            return image_bytes

        if image_path is None:
            raise VisualParsingError("Either image_path or image_bytes must be provided.")

        path = Path(image_path)
        if not path.exists():
            raise VisualParsingError(f"Image path does not exist: {path}")

        return await asyncio.to_thread(path.read_bytes)

    @staticmethod
    def _build_prompt(content_type: str, source_metadata: dict[str, Any] | None) -> str:
        """Compose system + user instruction block for forensic extraction."""

        user_prompt = build_visual_parse_user_prompt(
            content_type=content_type,
            source_metadata=source_metadata,
        )
        return f"{FORENSIC_VISUAL_SYSTEM_PROMPT}\n\n{user_prompt}"
