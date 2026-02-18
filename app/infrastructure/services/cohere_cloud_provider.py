from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, cast

import aiohttp
import structlog

from app.core.settings import settings
from app.domain.interfaces.embedding_provider import IEmbeddingProvider

logger = structlog.get_logger(__name__)


class CohereCloudProvider(IEmbeddingProvider):
    """Cohere embeddings provider with shared HTTP session."""

    def __init__(self, api_key: str):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(
            getattr(settings, "COHERE_EMBED_URL", "https://api.cohere.com/v2/embed")
        )
        self._model_name = str(getattr(settings, "COHERE_EMBED_MODEL", "embed-multilingual-v3.0"))
        self._dimensions = int(getattr(settings, "COHERE_EMBEDDING_DIMENSIONS", 1024) or 1024)
        self._session: Optional[aiohttp.ClientSession] = None
        self._post_semaphore = asyncio.Semaphore(
            max(1, int(getattr(settings, "COHERE_REQUEST_MAX_PARALLEL", 2) or 2))
        )

    @property
    def provider_name(self) -> str:
        return "cohere"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def embedding_dimensions(self) -> int:
        return self._dimensions

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        if not texts:
            return []
        inputs = [str(text or "") for text in texts]
        input_type = "search_query" if str(task or "") == "retrieval.query" else "search_document"
        payload = {
            "model": self._model_name,
            "texts": inputs,
            "input_type": input_type,
            "embedding_types": ["float"],
            "truncate": "END",
        }

        session = await self._get_session()
        async with self._post_semaphore:
            async with session.post(self.base_url, json=payload) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"cohere_embed_error:{response.status}:{body[:240]}")
                data = await response.json()

        embeddings_obj = data.get("embeddings") if isinstance(data, dict) else None
        float_vectors_raw = (
            embeddings_obj.get("float") if isinstance(embeddings_obj, dict) else None
        )
        float_vectors = float_vectors_raw if isinstance(float_vectors_raw, list) else []

        vectors: List[List[float]] = []
        for vec in float_vectors:
            if isinstance(vec, list):
                vectors.append([float(v) for v in vec])
        if len(vectors) == len(inputs):
            return vectors

        logger.warning(
            "cohere_embed_unexpected_shape",
            requested=len(inputs),
            received=len(vectors),
        )
        if vectors:
            return vectors + [
                [0.0] * self._dimensions for _ in range(max(0, len(inputs) - len(vectors)))
            ]
        return [[0.0] * self._dimensions for _ in inputs]

    async def chunk_and_encode(self, text: str) -> List[Dict[str, Any]]:
        chunks = self._split_text_simple(str(text or ""), limit=1000)
        if not chunks:
            return []
        vectors = await self.embed(chunks, task="retrieval.passage")
        out: List[Dict[str, Any]] = []
        offset = 0
        for chunk, vector in zip(chunks, vectors):
            out.append(
                {
                    "content": chunk,
                    "embedding": cast(List[float], vector),
                    "char_start": offset,
                    "char_end": offset + len(chunk),
                }
            )
            offset += len(chunk)
        return out

    @staticmethod
    def _split_text_simple(text: str, limit: int = 1000) -> List[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        parts: List[str] = []
        while len(cleaned) > limit:
            split_at = cleaned.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            parts.append(cleaned[:split_at])
            cleaned = cleaned[split_at:].strip()
        if cleaned:
            parts.append(cleaned)
        return parts
