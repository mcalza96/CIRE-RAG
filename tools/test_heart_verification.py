"""Unit tests for HEART verification in visual parser."""

from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy modules before importing any app code.
# ---------------------------------------------------------------------------
_fake_embedding = types.ModuleType("app.services.embedding_service")


class _JinaStub:
    @classmethod
    def get_instance(cls):
        return None


_fake_embedding.JinaEmbeddingService = _JinaStub
sys.modules.setdefault("app.services.embedding_service", _fake_embedding)

from app.ai.schemas import VerificationResult, VisualParseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PARSE = VisualParseResult(
    dense_summary="Tabla de tolerancias ISO 9001",
    markdown_content="| Param | Value |\n|---|---|\n| Tol | 0.05 |",
    visual_metadata={"type": "table", "page": 1},
)


def _noop_cache_decorator(func):
    """No-op replacement for @cached_extraction so tests don't touch Supabase."""
    return func


def _fresh_parser_class():
    """Force-reimport VisualDocumentParser to pick up fresh env vars and patches."""
    # Force reload settings to avoid singleton stale state
    import app.infrastructure.settings as core_settings
    importlib.reload(core_settings)

    # Patch the cache decorator to a no-op BEFORE reloading the module
    import app.core.caching.middleware as cm
    original_decorator = cm.cached_extraction
    cm.cached_extraction = _noop_cache_decorator

    mod = importlib.import_module("app.services.ingestion.visual_parser")
    importlib.reload(mod)

    cm.cached_extraction = original_decorator
    return mod.VisualDocumentParser


class _FakeVLM:
    """Mock VLM with configurable responses per schema type."""

    def __init__(
        self,
        parse_result: VisualParseResult,
        verification_result: VerificationResult,
    ):
        self._parse_result = parse_result
        self._verification_result = verification_result

    def generate_structured_output(
        self,
        image_content: bytes | str,
        prompt: str,
        schema: type,
        mime_type: str = "image/png",
    ) -> Any:
        if schema is VerificationResult:
            return self._verification_result
        return self._parse_result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verifier_passes_valid_extraction() -> None:
    """When verification passes, parser returns result with 'verified' status."""

    vlm = _FakeVLM(
        parse_result=SAMPLE_PARSE.model_copy(deep=True),
        verification_result=VerificationResult(is_valid=True, discrepancies=[]),
    )

    with patch.dict(os.environ, {"ENABLE_HEART_VERIFICATION": "true"}):
        ParserClass = _fresh_parser_class()
        parser = ParserClass(model=vlm, max_retries=1)

        result = await parser.parse_image(
            image_bytes=b"fake-png-bytes",
            content_type="table",
        )

    assert isinstance(result, VisualParseResult)
    assert result.visual_metadata.get("verification_status") == "verified"
    assert result.dense_summary == SAMPLE_PARSE.dense_summary


@pytest.mark.asyncio
async def test_verifier_detects_discrepancy_and_retries() -> None:
    """When verification fails, parser retries with negative feedback."""

    bad_parse = VisualParseResult(
        dense_summary="Tabla de tolerancias ISO 9001",
        markdown_content="| Param | Value |\n|---|---|\n| Tol | 0.5 |",
        visual_metadata={"type": "table", "page": 1},
    )
    good_parse = VisualParseResult(
        dense_summary="Tabla de tolerancias ISO 9001",
        markdown_content="| Param | Value |\n|---|---|\n| Tol | 0.05 |",
        visual_metadata={"type": "table", "page": 1},
    )

    call_log: list[str] = []
    original_verification = VerificationResult(
        is_valid=False,
        discrepancies=["Cell B2: extracted '0.5', image shows '0.05'"],
    )

    class _RetryVLM:
        def __init__(self):
            self._parse_calls = 0
            self._verify_calls = 0

        def generate_structured_output(self, image, prompt, schema, mime="image/png"):
            if schema is VerificationResult:
                self._verify_calls += 1
                call_log.append(f"verify_{self._verify_calls}")
                if self._verify_calls == 1:
                    return original_verification
                return VerificationResult(is_valid=True, discrepancies=[])
            self._parse_calls += 1
            call_log.append(f"parse_{self._parse_calls}")
            if self._parse_calls == 1:
                return bad_parse.model_copy(deep=True)
            return good_parse.model_copy(deep=True)

    with patch.dict(os.environ, {"ENABLE_HEART_VERIFICATION": "true"}):
        ParserClass = _fresh_parser_class()
        parser = ParserClass(
            model=_RetryVLM(),
            max_retries=2,
            retry_delay_seconds=0.01,
        )

        result = await parser.parse_image(
            image_bytes=b"fake-png-bytes",
            content_type="table",
        )

    assert result.markdown_content == good_parse.markdown_content
    assert result.visual_metadata.get("verification_status") == "verified"
    assert "parse_1" in call_log
    assert "verify_1" in call_log
    assert "parse_2" in call_log
    assert "verify_2" in call_log


@pytest.mark.asyncio
async def test_verifier_disabled_by_default() -> None:
    """Without ENABLE_HEART_VERIFICATION, parser skips verification entirely."""

    vlm = _FakeVLM(
        parse_result=SAMPLE_PARSE.model_copy(deep=True),
        verification_result=VerificationResult(is_valid=False, discrepancies=["should not run"]),
    )

    env = os.environ.copy()
    env.pop("ENABLE_HEART_VERIFICATION", None)

    with patch.dict(os.environ, env, clear=True):
        ParserClass = _fresh_parser_class()
        parser = ParserClass(model=vlm, max_retries=0)

        result = await parser.parse_image(
            image_bytes=b"fake-png-bytes",
            content_type="table",
        )

    assert isinstance(result, VisualParseResult)
    assert "verification_status" not in result.visual_metadata
