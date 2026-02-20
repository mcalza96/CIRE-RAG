"""Caching middleware for visual extraction requests."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, cast

import structlog

from app.infrastructure.caching.image_hasher import ImageHasher
from app.ai.config import get_model_settings
from app.ai.schemas import VisualParseResult
from app.infrastructure.settings import settings
from app.infrastructure.supabase.client import get_async_supabase_client

logger = structlog.get_logger(__name__)

T = TypeVar("T")
AsyncMethod = Callable[..., Awaitable[T]]


class VlmBudgetExceededError(RuntimeError):
    """Raised when DEV daily VLM miss budget is exceeded."""


def cached_extraction(func: AsyncMethod[VisualParseResult]) -> AsyncMethod[VisualParseResult]:
    """Cache-aside decorator for `VisualDocumentParser.parse_image`.

    Cache key dimensions:
    - image_hash (SHA-256 over bytes)
    - provider
    - model_version
    - content_type
    - prompt_version
    - schema_version
    """

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> VisualParseResult:
        started = time.perf_counter()
        bound = _bind_call_arguments(func=func, self_obj=self, args=args, kwargs=kwargs)
        image_bytes = await _resolve_image_payload(self=self, kwargs=bound)
        image_hash = ImageHasher.sha256_bytes(image_bytes)

        provider, model_version = _resolve_provider_model()
        content_type, prompt_version, schema_version = _resolve_cache_dimensions(bound)
        cache_row = await _fetch_cache_row(
            image_hash=image_hash,
            provider=provider,
            model_version=model_version,
            content_type=content_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )

        if cache_row is not None:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            created_at = str(cache_row.get("created_at") or "")
            logger.info(
                "visual_extraction_cache_hit",
                image_hash=image_hash,
                provider=provider,
                model_version=model_version,
                content_type=content_type,
                prompt_version=prompt_version,
                schema_version=schema_version,
                duration_ms=elapsed_ms,
                cache_age_seconds=_safe_cache_age_seconds(created_at),
            )
            result_data = cache_row.get("result_data") or {}
            parsed = VisualParseResult.model_validate(result_data)
            return _annotate_result(
                parsed,
                image_hash=image_hash,
                provider=provider,
                model_version=model_version,
                content_type=content_type,
                prompt_version=prompt_version,
                schema_version=schema_version,
                cache_hit=True,
                duration_ms=elapsed_ms,
                cache_created_at=created_at,
            )

        await _enforce_dev_daily_budget(provider=provider, model_version=model_version)

        logger.info(
            "visual_extraction_cache_miss",
            image_hash=image_hash,
            provider=provider,
            model_version=model_version,
        )

        result = await func(self, *args, **kwargs)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        token_usage = _extract_token_usage(bound)
        await _store_cache_row(
            image_hash=image_hash,
            provider=provider,
            model_version=model_version,
            content_type=content_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
            result_data=result.model_dump(mode="json"),
            token_usage=token_usage,
        )
        return _annotate_result(
            result,
            image_hash=image_hash,
            provider=provider,
            model_version=model_version,
            content_type=content_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
            cache_hit=False,
            duration_ms=elapsed_ms,
            cache_created_at=None,
        )

    return cast(AsyncMethod[VisualParseResult], wrapper)


async def _resolve_image_payload(self: Any, kwargs: dict[str, Any]) -> bytes:
    """Reuse parser payload resolution to hash canonical bytes."""

    image_bytes = kwargs.get("image_bytes")
    image_path = kwargs.get("image_path")

    resolver = getattr(self, "_resolve_image_payload", None)
    if callable(resolver):
        resolved = resolver(image_path=image_path, image_bytes=image_bytes)
        if asyncio.iscoroutine(resolved):
            return await resolved
        if isinstance(resolved, bytes):
            return resolved
        raise ValueError("_resolve_image_payload must return bytes.")

    if isinstance(image_bytes, bytes) and image_bytes:
        return image_bytes

    if image_path is None:
        raise ValueError("Either image_path or image_bytes is required for cache hashing.")

    return await asyncio.to_thread(Path(image_path).read_bytes)


def _resolve_provider_model() -> tuple[str, str]:
    """Resolve current ingest provider/model from validated settings."""

    settings = get_model_settings()
    provider = settings.resolved_ingest_provider.value
    model_version = settings.resolved_ingest_model_name
    return provider, model_version


def _resolve_cache_dimensions(kwargs: dict[str, Any]) -> tuple[str, str, str]:
    content_type = str(kwargs.get("content_type") or "table").strip().lower()
    prompt_version = str(settings.VISUAL_CACHE_PROMPT_VERSION or "v1").strip()
    schema_version = str(settings.VISUAL_CACHE_SCHEMA_VERSION or "VisualParseResult:v1").strip()
    return content_type, prompt_version, schema_version


async def _fetch_cache_row(
    image_hash: str,
    provider: str,
    model_version: str,
    content_type: str,
    prompt_version: str,
    schema_version: str,
) -> dict[str, Any] | None:
    """Fetch cached row by deterministic cache key."""

    client = await get_async_supabase_client()
    use_v2 = bool(settings.VISUAL_CACHE_KEY_V2_ENABLED)
    if use_v2:
        try:
            response = await (
                client.table("cache_visual_extractions")
                .select("result_data,created_at")
                .eq("image_hash", image_hash)
                .eq("provider", provider)
                .eq("model_version", model_version)
                .eq("content_type", content_type)
                .eq("prompt_version", prompt_version)
                .eq("schema_version", schema_version)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("visual_cache_v2_lookup_failed_fallback_v1", error=str(exc))
            response = await (
                client.table("cache_visual_extractions")
                .select("result_data,created_at")
                .eq("image_hash", image_hash)
                .eq("provider", provider)
                .eq("model_version", model_version)
                .limit(1)
                .execute()
            )
    else:
        response = await (
            client.table("cache_visual_extractions")
            .select("result_data,created_at")
            .eq("image_hash", image_hash)
            .eq("provider", provider)
            .eq("model_version", model_version)
            .limit(1)
            .execute()
        )
    rows = (getattr(response, "data", None) or [])
    if isinstance(rows, list) and rows:
        first = rows[0]
        return first if isinstance(first, dict) else None
    return None


def _annotate_result(
    result: VisualParseResult,
    *,
    image_hash: str,
    provider: str,
    model_version: str,
    content_type: str,
    prompt_version: str,
    schema_version: str,
    cache_hit: bool,
    duration_ms: float,
    cache_created_at: str | None,
) -> VisualParseResult:
    annotated = result.model_copy(deep=True)
    metadata = annotated.visual_metadata if isinstance(annotated.visual_metadata, dict) else {}
    metadata["cache_hit"] = cache_hit
    metadata["cache_key"] = {
        "image_hash": image_hash,
        "provider": provider,
        "model_version": model_version,
        "content_type": content_type,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    metadata["parse_duration_ms"] = duration_ms
    if cache_created_at:
        metadata["cache_created_at"] = cache_created_at
    annotated.visual_metadata = metadata
    return annotated


def _safe_cache_age_seconds(created_at: str) -> float | None:
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - created.astimezone(timezone.utc)
        return round(max(age.total_seconds(), 0.0), 2)
    except Exception:
        return None


async def _store_cache_row(
    image_hash: str,
    provider: str,
    model_version: str,
    content_type: str,
    prompt_version: str,
    schema_version: str,
    result_data: dict[str, Any],
    token_usage: dict[str, Any] | None,
) -> None:
    """Persist extraction output for future cache hits."""

    client = await get_async_supabase_client()
    payload = {
        "image_hash": image_hash,
        "provider": provider,
        "model_version": model_version,
        "content_type": content_type,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "result_data": result_data,
        "token_usage": token_usage,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    use_v2 = bool(settings.VISUAL_CACHE_KEY_V2_ENABLED)
    try:
        await client.table("cache_visual_extractions").upsert(
            payload,
            on_conflict=(
                "image_hash,provider,model_version,content_type,prompt_version,schema_version"
                if use_v2
                else "image_hash,provider,model_version"
            ),
        ).execute()
    except Exception as exc:
        if use_v2:
            logger.warning("visual_cache_v2_write_failed_fallback_v1", error=str(exc))
            legacy_payload = {
                "image_hash": image_hash,
                "provider": provider,
                "model_version": model_version,
                "result_data": result_data,
                "token_usage": token_usage,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await client.table("cache_visual_extractions").upsert(
                legacy_payload,
                on_conflict="image_hash,provider,model_version",
            ).execute()
            return
        raise


def _extract_token_usage(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Extract optional token/cost metadata from parse call kwargs."""

    token_usage = kwargs.get("token_usage")
    if isinstance(token_usage, dict):
        return token_usage

    source_metadata = kwargs.get("source_metadata")
    if isinstance(source_metadata, dict):
        usage = source_metadata.get("token_usage")
        if isinstance(usage, dict):
            return usage

    return None


def _bind_call_arguments(
    func: AsyncMethod[VisualParseResult],
    self_obj: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind positional + keyword args into a single name->value mapping."""

    signature = inspect.signature(func)
    bound = signature.bind_partial(self_obj, *args, **kwargs)
    arguments = dict(bound.arguments)
    arguments.pop("self", None)
    return arguments


async def _enforce_dev_daily_budget(provider: str, model_version: str) -> None:
    """Guard rail: in DEV, block new misses if daily cap is reached."""

    environment = settings.ENVIRONMENT.strip().lower()
    if environment not in {"dev", "development", "local"}:
        return

    daily_limit = settings.DAILY_VLM_LIMIT
    if daily_limit is None:
        return

    if daily_limit <= 0:
        return

    client = await get_async_supabase_client()
    now_utc = datetime.now(timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    response = await (
        client.table("cache_visual_extractions")
        .select("image_hash", count=cast(Any, "exact"))
        .eq("provider", provider)
        .eq("model_version", model_version)
        .gte("created_at", day_start)
        .execute()
    )

    used = int(response.count or 0)
    if used >= daily_limit:
        raise VlmBudgetExceededError(
            f"DAILY_VLM_LIMIT reached ({used}/{daily_limit}) for {provider}:{model_version}."
        )
