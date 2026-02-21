"""
Centralized AI Embedding Service.
Handles multi-modal or text embeddings, provider selection (Cloud vs Local), and caching.
"""

from __future__ import annotations
import asyncio
import structlog
import threading
import time
from collections import OrderedDict
from typing import List, Dict, Any, Optional, cast

from app.infrastructure.observability.metrics import track_span
from app.infrastructure.settings import settings
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.ai.providers.cohere import CohereCloudProvider
from app.ai.providers.jina_cloud import JinaCloudProvider

logger = structlog.get_logger(__name__)


class EmbeddingService:
    """
    Facade Service for Embeddings.
    Handles caching, provider selection (Cloud vs Local), and metrics.
    """

    _instance: Optional["EmbeddingService"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EmbeddingService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.Lock()
        with self._lock:
            if getattr(self, "_initialized", False):
                return

            self.local_provider: Optional[IEmbeddingProvider] = None
            self.is_deployed_environment = settings.is_deployed_environment
            jina_key = str(settings.JINA_API_KEY or "")
            cohere_key = str(getattr(settings, "COHERE_API_KEY", "") or "")

            # Providers
            self.jina_cloud_provider: Optional[IEmbeddingProvider] = (
                JinaCloudProvider(api_key=jina_key) if jina_key else None
            )
            self.cloud_provider = self.jina_cloud_provider
            self.cohere_cloud_provider: Optional[IEmbeddingProvider] = (
                CohereCloudProvider(api_key=cohere_key) if cohere_key else None
            )

            # Default behavior from env
            self.default_mode = str(settings.JINA_MODE or "CLOUD").upper()
            self.default_provider = str(getattr(settings, "EMBEDDING_PROVIDER_DEFAULT", "jina") or "jina").strip().lower()

            ingest_override = getattr(settings, "INGEST_EMBED_PROVIDER_DEFAULT", None)
            if ingest_override:
                self.ingest_default_provider = str(ingest_override).strip().lower()
            elif self.cohere_cloud_provider is not None:
                self.ingest_default_provider = "cohere"
            else:
                self.ingest_default_provider = self.default_provider

            allowlist_raw = str(getattr(settings, "EMBEDDING_PROVIDER_ALLOWLIST", "jina,cohere") or "")
            self.allowed_providers = {
                p.strip().lower() for p in allowlist_raw.split(",") if p.strip()
            } or {"jina", "cohere"}

            if self.is_deployed_environment:
                self.default_mode = "CLOUD"
                if self.default_provider == "jina" and not self.jina_cloud_provider:
                    raise RuntimeError("Deployed environment requires Jina cloud credentials when EMBEDDING_PROVIDER_DEFAULT=jina.")
                if self.default_provider == "cohere" and not self.cohere_cloud_provider:
                    raise RuntimeError("Deployed environment requires Cohere credentials when EMBEDDING_PROVIDER_DEFAULT=cohere.")
            elif self.default_mode == "LOCAL":
                self.local_provider = self._build_local_provider()
            elif self.default_provider == "jina" and self.default_mode == "CLOUD" and not self.jina_cloud_provider:
                logger.warning("cloud_mode_without_api_key_fallback_local")
                self.default_mode = "LOCAL"

            # Caching for query embeddings
            self._cache: OrderedDict[tuple[str, str], tuple[list[float], float]] = OrderedDict()
            self._cache_max_size = max(100, int(getattr(settings, "EMBEDDING_CACHE_MAX_SIZE", 4000)))
            self._cache_ttl_seconds = max(30, int(getattr(settings, "EMBEDDING_CACHE_TTL_SECONDS", 1800) or 1800))
            self._cache_lock = threading.Lock()

            self.embedding_concurrency = max(1, int(getattr(settings, "EMBEDDING_CONCURRENCY", 5)))
            self._embedding_semaphore = asyncio.Semaphore(self.embedding_concurrency)
            self._initialized = True

    def _build_local_provider(self) -> IEmbeddingProvider:
        try:
            from app.ai.providers.jina_local import JinaLocalProvider
            return JinaLocalProvider()
        except Exception as exc:
            raise RuntimeError("Failed to initialize local embedding provider.") from exc

    def _resolve_provider_name(self, provider: Optional[str] = None) -> str:
        selected = str(provider or self.default_provider or "jina").strip().lower()
        if selected not in self.allowed_providers:
            selected = self.default_provider
        return selected

    @staticmethod
    def _is_technical_provider_error(exc: Exception) -> bool:
        markers = ("timeout", "connection", "rate limit", "503", "502", "504")
        text = str(exc or "").lower()
        return any(marker in text for marker in markers)

    def _resolve_ingest_fallback_provider(self, requested_provider: Optional[str]) -> Optional[str]:
        if not bool(getattr(settings, "INGEST_EMBED_FALLBACK_ON_TECHNICAL_ERROR", True)):
            return None
        primary = self._resolve_provider_name(requested_provider)
        fallback = str(getattr(settings, "INGEST_EMBED_FALLBACK_PROVIDER", "jina") or "jina").strip().lower()
        return fallback if fallback != primary and fallback in self.allowed_providers else None

    def _get_provider(self, mode: Optional[str] = None, provider: Optional[str] = None) -> IEmbeddingProvider:
        provider_name = self._resolve_provider_name(provider)
        resolved_mode = str(mode or self.default_mode or "CLOUD").upper()

        if self.is_deployed_environment and resolved_mode == "LOCAL":
            resolved_mode = "CLOUD"

        if provider_name == "cohere":
            if self.cohere_cloud_provider: return self.cohere_cloud_provider
            raise RuntimeError("Cohere provider unavailable.")

        if resolved_mode == "CLOUD":
            if self.jina_cloud_provider: return self.jina_cloud_provider
            if not self.is_deployed_environment: logger.warning("cloud_fallback_local")
            else: raise RuntimeError("Cloud provider unavailable in production.")

        if self.local_provider is None:
            self.local_provider = self._build_local_provider()
        return self.local_provider

    def resolve_embedding_profile(self, *, provider: Optional[str] = None, mode: Optional[str] = None) -> Dict[str, Any]:
        p = self._get_provider(mode=mode, provider=provider)
        profile = p.profile()
        if p.provider_name == "jina":
            profile["mode"] = str(mode or self.default_mode or "CLOUD").upper()
        return profile

    def resolve_ingestion_profile(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        meta = metadata or {}
        p = meta.get("embedding_provider") or self.ingest_default_provider
        m = meta.get("embedding_mode") or meta.get("jina_mode")
        profile = self.resolve_embedding_profile(provider=str(p) if p else None, mode=str(m) if m else None)
        profile.update({
            "fallback_provider": self._resolve_ingest_fallback_provider(str(p) if p else None),
            "fallback_on_technical_error": bool(getattr(settings, "INGEST_EMBED_FALLBACK_ON_TECHNICAL_ERROR", True))
        })
        return profile

    @track_span(name="span:embedding_generation")
    async def embed_texts(self, texts: List[str], task: str = "retrieval.passage", mode: Optional[str] = None, provider: Optional[str] = None) -> List[List[float]]:
        if not texts: return []
        is_query = task == "retrieval.query"
        final: List[Optional[List[float]]] = [None] * len(texts)
        missing_indices = []
        missing_texts = []

        if is_query:
            with self._cache_lock:
                now = time.monotonic()
                for i, t in enumerate(texts):
                    cached = self._cache.get((t, task))
                    if cached and cached[1] > now:
                        final[i] = cached[0]
                        self._cache.move_to_end((t, task))
                    else:
                        missing_indices.append(i); missing_texts.append(t)
        else:
            missing_indices = list(range(len(texts))); missing_texts = texts

        if not missing_texts: return cast(List[List[float]], final)

        unique_texts, unique_keys, key_to_idx = [], [], {}
        for idx, txt in zip(missing_indices, missing_texts):
            key = (txt, task)
            if key not in key_to_idx:
                unique_texts.append(txt); unique_keys.append(key); key_to_idx[key] = []
            key_to_idx[key].append(idx)

        p = self._get_provider(mode=mode, provider=provider)
        try:
            async with self._embedding_semaphore:
                embs = await p.embed(unique_texts, task=task)
            for key, emb in zip(unique_keys, embs):
                for idx in key_to_idx[key]: final[idx] = emb
                if is_query:
                    with self._cache_lock:
                        self._cache[key] = (emb, time.monotonic() + self._cache_ttl_seconds)
                        self._cache.move_to_end(key)
                        while len(self._cache) > self._cache_max_size: self._cache.popitem(last=False)
            return cast(List[List[float]], final)
        except Exception as e:
            fallback = self._resolve_ingest_fallback_provider(provider)
            if task != "retrieval.query" and fallback and self._resolve_provider_name(provider) == "cohere" and self._is_technical_provider_error(e):
                fp = self._get_provider(mode=mode, provider=fallback)
                async with self._embedding_semaphore:
                    embs = await fp.embed(unique_texts, task=task)
                for key, emb in zip(unique_keys, embs):
                    for idx in key_to_idx[key]: final[idx] = emb
                return cast(List[List[float]], final)
            raise e

    @track_span(name="span:late_chunking")
    async def chunk_and_encode(self, text: str, mode: Optional[str] = None, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        p = self._get_provider(mode=mode, provider=provider)
        try:
            async with self._embedding_semaphore: return await p.chunk_and_encode(text)
        except Exception as e:
            fallback = self._resolve_ingest_fallback_provider(provider)
            if fallback and self._resolve_provider_name(provider) == "cohere" and self._is_technical_provider_error(e):
                fp = self._get_provider(mode=mode, provider=fallback)
                async with self._embedding_semaphore: return await fp.chunk_and_encode(text)
            raise e

# Alias
JinaEmbeddingService = EmbeddingService
