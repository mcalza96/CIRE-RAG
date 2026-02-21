from __future__ import annotations

from typing import Any

import aiohttp
import structlog

from app.ai.contracts import AIModelConfig
from app.infrastructure.settings import settings

logger = structlog.get_logger(__name__)

# Default minimum relevance score from the cross-encoder.
# Results below this are considered noise and pruned.
_DEFAULT_RERANK_MIN_RELEVANCE = 0.15


class JinaReranker:
    """Semantic reranker backed by Jina rerank API."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        rerank_url: str | None = None,
        timeout_seconds: int = 8,
    ):
        self._api_key = api_key or AIModelConfig.JINA_API_KEY
        self._model_name = model_name or AIModelConfig.JINA_RERANK_MODEL
        self._rerank_url = rerank_url or AIModelConfig.JINA_RERANK_URL
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        pass

    def is_enabled(self) -> bool:
        return bool(self._api_key and self._rerank_url and self._model_name)

    async def rerank_documents(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[dict[str, Any]]:
        if not self.is_enabled() or not query.strip() or not documents:
            return []

        payload = {
            "model": self._model_name,
            "query": query,
            "documents": documents,
            "top_n": max(1, min(top_n, len(documents))),
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(self._rerank_url, json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"jina_rerank_http_{response.status}: {body[:300]}")
                data = await response.json()

        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []

        min_relevance = float(
            getattr(settings, "RERANK_MIN_RELEVANCE_SCORE", _DEFAULT_RERANK_MIN_RELEVANCE)
            or _DEFAULT_RERANK_MIN_RELEVANCE
        )
        return [
            row for row in rows
            if isinstance(row, dict)
            and float(row.get("relevance_score", 0.0) or 0.0) >= min_relevance
        ]
