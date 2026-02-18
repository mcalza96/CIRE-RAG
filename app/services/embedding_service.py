import structlog
import threading
import asyncio
import time
from collections import OrderedDict
from typing import List, Dict, Any, Optional, cast
from app.core.observability.metrics import track_span
from app.core.settings import settings
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.infrastructure.services.jina_cloud_provider import JinaCloudProvider

logger = structlog.get_logger(__name__)


class JinaEmbeddingService:
    """
    Facade Service for Embeddings.
    Handles caching, provider selection (Cloud vs Local), and metrics.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(JinaEmbeddingService, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        with self._lock:
            if self._initialized:
                return

            self.local_provider: Optional[IEmbeddingProvider] = None
            self.is_deployed_environment = settings.is_deployed_environment

            # Providers
            self.cloud_provider: Optional[IEmbeddingProvider] = (
                JinaCloudProvider(api_key=settings.JINA_API_KEY) if settings.JINA_API_KEY else None
            )

            # Default behavior from env
            self.default_mode = str(settings.JINA_MODE or "CLOUD").upper()

            if self.is_deployed_environment:
                self.default_mode = "CLOUD"
                if not self.cloud_provider:
                    raise RuntimeError(
                        "Deployed environment requires JINA_MODE=CLOUD with JINA_API_KEY configured."
                    )
            elif self.default_mode == "LOCAL":
                self.local_provider = self._build_local_provider()
            elif self.default_mode == "CLOUD" and not self.cloud_provider:
                logger.warning("cloud_mode_without_api_key_fallback_local")
                self.default_mode = "LOCAL"

            # Caching for query embeddings
            self._cache: "OrderedDict[tuple[str, str], tuple[list[float], float]]" = OrderedDict()
            self._cache_max_size = max(
                100, int(getattr(settings, "EMBEDDING_CACHE_MAX_SIZE", 4000))
            )
            self._cache_ttl_seconds = max(
                30,
                int(getattr(settings, "EMBEDDING_CACHE_TTL_SECONDS", 1800) or 1800),
            )
            self._cache_lock = threading.Lock()

            # Throughput controls
            self.embedding_concurrency = max(1, int(getattr(settings, "EMBEDDING_CONCURRENCY", 5)))
            self._embedding_semaphore = asyncio.Semaphore(self.embedding_concurrency)

            self._initialized = True

    @classmethod
    def get_instance(cls):
        return cls()  # Singleton

    def _build_local_provider(self) -> IEmbeddingProvider:
        try:
            from app.infrastructure.services.jina_local_provider import JinaLocalProvider
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize local embedding provider. "
                "Install requirements-local.txt or set JINA_MODE=CLOUD with JINA_API_KEY."
            ) from exc
        return JinaLocalProvider()

    def _get_provider(self, mode: Optional[str] = None) -> IEmbeddingProvider:
        resolved_mode = str(mode or self.default_mode or "CLOUD").upper()

        if self.is_deployed_environment:
            if resolved_mode == "LOCAL":
                logger.warning("local_mode_blocked_in_deployed_environment")
            resolved_mode = "CLOUD"

        if resolved_mode == "CLOUD":
            if self.cloud_provider:
                return self.cloud_provider
            if self.is_deployed_environment:
                raise RuntimeError(
                    "Cloud embedding provider unavailable in deployed environment. "
                    "Set JINA_API_KEY or use local environment for LOCAL mode."
                )
            logger.warning("cloud_provider_unavailable_fallback_local")

        if self.local_provider is None:
            self.local_provider = self._build_local_provider()

        return self.local_provider

    @track_span(name="span:embedding_generation")
    async def embed_texts(
        self, texts: List[str], task: str = "retrieval.passage", mode: Optional[str] = None
    ) -> List[List[float]]:
        if not texts:
            return []

        # Cache logic
        is_query_task = task == "retrieval.query"
        final_embeddings: List[Optional[List[float]]] = [None] * len(texts)
        missing_indices = []
        missing_texts = []

        if is_query_task:
            with self._cache_lock:
                now = time.monotonic()
                for i, t in enumerate(texts):
                    cache_key = (t, task)
                    cached = self._cache.get(cache_key)
                    if cached is not None:
                        embedding, expires_at = cached
                        if expires_at > now:
                            final_embeddings[i] = embedding
                            # Move to end (LRU)
                            self._cache.move_to_end(cache_key)
                            continue
                        self._cache.pop(cache_key, None)
                    if final_embeddings[i] is None:
                        missing_indices.append(i)
                        missing_texts.append(t)
        else:
            missing_indices = list(range(len(texts)))
            missing_texts = texts

        if not missing_texts:
            return cast(List[List[float]], final_embeddings)

        # Dedupe repeated query texts in the same request.
        unique_texts: List[str] = []
        unique_keys: List[tuple[str, str]] = []
        key_to_indices: Dict[tuple[str, str], List[int]] = {}
        for idx, txt in zip(missing_indices, missing_texts):
            key = (txt, task)
            if key not in key_to_indices:
                key_to_indices[key] = []
                unique_texts.append(txt)
                unique_keys.append(key)
            key_to_indices[key].append(idx)

        # Delegate to provider
        provider = self._get_provider(mode)
        try:
            async with self._embedding_semaphore:
                embeddings = await provider.embed(unique_texts, task=task)

            # Update cache and merge results
            for key, emb in zip(unique_keys, embeddings):
                for idx in key_to_indices.get(key, []):
                    final_embeddings[idx] = emb

                if is_query_task:
                    with self._cache_lock:
                        expires_at = time.monotonic() + float(self._cache_ttl_seconds)
                        self._cache[key] = (emb, expires_at)
                        self._cache.move_to_end(key)
                        while len(self._cache) > self._cache_max_size:
                            self._cache.popitem(last=False)

            return cast(List[List[float]], final_embeddings)
        except Exception as e:
            logger.error("embedding_generation_failed", error=str(e), texts_count=len(texts))
            raise e

    @track_span(name="span:late_chunking")
    async def chunk_and_encode(self, text: str, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        provider = self._get_provider(mode)
        try:
            async with self._embedding_semaphore:
                return await provider.chunk_and_encode(text)
        except Exception as e:
            logger.error(f"Error in chunk_and_encode: {e}")
            raise e
