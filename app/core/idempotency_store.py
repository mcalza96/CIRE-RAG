from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import structlog

from app.core.settings import settings

logger = structlog.get_logger(__name__)


class _InMemoryIdempotencyStore:
    def __init__(self, ttl_seconds: int):
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        now = time.time()
        async with self._lock:
            self._prune(now)
            row = self._cache.get(key)
            if not row:
                return None
            self._cache[key] = (now, row[1])
            return row[1]

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        now = time.time()
        async with self._lock:
            self._prune(now)
            self._cache[key] = (now, payload)

    async def reset_for_tests(self) -> None:
        async with self._lock:
            self._cache.clear()

    def _prune(self, now: float) -> None:
        stale = [k for k, (ts, _) in self._cache.items() if now - ts > self._ttl_seconds]
        for key in stale:
            self._cache.pop(key, None)


class _RedisIdempotencyStore:
    def __init__(self, redis_client: Any, ttl_seconds: int):
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        raw = await self._redis.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def set(self, key: str, payload: dict[str, Any]) -> None:
        await self._redis.set(key, json.dumps(payload), ex=self._ttl_seconds)

    async def reset_for_tests(self) -> None:
        return None


_store_singleton: Any = None
_store_init_lock = asyncio.Lock()


async def get_idempotency_store(ttl_seconds: int = 600) -> Any:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton

    async with _store_init_lock:
        if _store_singleton is not None:
            return _store_singleton

        redis_url = str(settings.REDIS_URL or "").strip()
        if redis_url:
            try:
                from redis import asyncio as redis_async

                client = redis_async.from_url(redis_url, decode_responses=True)
                await client.ping()
                _store_singleton = _RedisIdempotencyStore(client, ttl_seconds)
                logger.info("idempotency_store_initialized", backend="redis")
                return _store_singleton
            except Exception as exc:
                logger.warning("idempotency_store_redis_unavailable_fallback_memory", error=str(exc))

        _store_singleton = _InMemoryIdempotencyStore(ttl_seconds)
        logger.info("idempotency_store_initialized", backend="memory")
        return _store_singleton


async def reset_idempotency_store_for_tests() -> None:
    store = await get_idempotency_store()
    await store.reset_for_tests()
