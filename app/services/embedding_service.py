import structlog
import threading
import asyncio
import time
from collections import OrderedDict
from typing import List, Dict, Any, Optional, cast
from app.core.observability.metrics import track_span
from app.core.settings import settings
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.infrastructure.services.cohere_cloud_provider import CohereCloudProvider
from app.infrastructure.services.jina_cloud_provider import JinaCloudProvider

logger = structlog.get_logger(__name__)


class JinaEmbeddingService:
    """
    Facade Service for Embeddings.
    Handles caching, provider selection (Cloud vs Local), and metrics.
    """

    _instance: Optional["JinaEmbeddingService"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "JinaEmbeddingService":
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
            self.default_provider = (
                str(getattr(settings, "EMBEDDING_PROVIDER_DEFAULT", "jina") or "jina")
                .strip()
                .lower()
            )
            ingest_override = getattr(settings, "INGEST_EMBED_PROVIDER_DEFAULT", None)
            if ingest_override:
                self.ingest_default_provider = str(ingest_override).strip().lower()
            elif self.cohere_cloud_provider is not None:
                self.ingest_default_provider = "cohere"
            else:
                self.ingest_default_provider = self.default_provider
            allowlist_raw = str(
                getattr(settings, "EMBEDDING_PROVIDER_ALLOWLIST", "jina,cohere") or ""
            )
            self.allowed_providers = {
                p.strip().lower() for p in allowlist_raw.split(",") if p.strip()
            } or {"jina", "cohere"}

            if self.is_deployed_environment:
                self.default_mode = "CLOUD"
                if self.default_provider == "jina" and not self.jina_cloud_provider:
                    raise RuntimeError(
                        "Deployed environment requires Jina cloud credentials when EMBEDDING_PROVIDER_DEFAULT=jina."
                    )
                if self.default_provider == "cohere" and not self.cohere_cloud_provider:
                    raise RuntimeError(
                        "Deployed environment requires Cohere credentials when EMBEDDING_PROVIDER_DEFAULT=cohere."
                    )
            elif self.default_mode == "LOCAL":
                self.local_provider = self._build_local_provider()
            elif (
                self.default_provider == "jina"
                and self.default_mode == "CLOUD"
                and not self.jina_cloud_provider
            ):
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

    def _build_local_provider(self) -> IEmbeddingProvider:
        try:
            from app.infrastructure.services.jina_local_provider import JinaLocalProvider
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize local embedding provider. "
                "Install requirements-local.txt or set JINA_MODE=CLOUD with JINA_API_KEY."
            ) from exc
        return JinaLocalProvider()

    def _resolve_provider_name(self, provider: Optional[str] = None) -> str:
        selected = str(provider or self.default_provider or "jina").strip().lower()
        if selected not in self.allowed_providers:
            logger.warning(
                "embedding_provider_not_allowed_defaulting",
                requested=selected,
                default=self.default_provider,
            )
            selected = self.default_provider
        return selected

    @staticmethod
    def _is_technical_provider_error(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        text = str(exc or "").lower()
        markers = (
            "timeout",
            "timed out",
            "connection",
            "readerror",
            "connecterror",
            "429",
            "rate limit",
            "503",
            "502",
            "504",
            "temporarily unavailable",
        )
        if any(marker in name for marker in markers):
            return True
        if any(marker in text for marker in markers):
            return True
        return False

    def _resolve_ingest_fallback_provider(self, requested_provider: Optional[str]) -> Optional[str]:
        if not bool(getattr(settings, "INGEST_EMBED_FALLBACK_ON_TECHNICAL_ERROR", True)):
            return None
        primary = self._resolve_provider_name(requested_provider)
        fallback = (
            str(getattr(settings, "INGEST_EMBED_FALLBACK_PROVIDER", "jina") or "jina")
            .strip()
            .lower()
        )
        if fallback == primary:
            return None
        if fallback not in self.allowed_providers:
            return None
        return fallback

    def _get_provider(
        self, mode: Optional[str] = None, provider: Optional[str] = None
    ) -> IEmbeddingProvider:
        provider_name = self._resolve_provider_name(provider)
        resolved_mode = str(mode or self.default_mode or "CLOUD").upper()

        if self.is_deployed_environment:
            if provider_name == "jina" and resolved_mode == "LOCAL":
                logger.warning("local_mode_blocked_in_deployed_environment")
                resolved_mode = "CLOUD"

        if provider_name == "cohere":
            if self.cohere_cloud_provider:
                return self.cohere_cloud_provider
            raise RuntimeError("Cohere embedding provider unavailable. Set COHERE_API_KEY.")

        if resolved_mode == "CLOUD":
            if self.jina_cloud_provider:
                return self.jina_cloud_provider
            if self.is_deployed_environment:
                raise RuntimeError(
                    "Cloud embedding provider unavailable in deployed environment. "
                    "Set JINA_API_KEY or use local environment for LOCAL mode."
                )
            logger.warning("cloud_provider_unavailable_fallback_local")

        if self.local_provider is None:
            self.local_provider = self._build_local_provider()

        return self.local_provider

    def resolve_embedding_profile(
        self,
        *,
        provider: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        runtime_provider = self._get_provider(mode=mode, provider=provider)
        profile = runtime_provider.profile()
        if runtime_provider.provider_name == "jina":
            profile["mode"] = str(mode or self.default_mode or "CLOUD").upper()
        return profile

    def resolve_ingestion_profile(
        self, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        meta = metadata if isinstance(metadata, dict) else {}
        requested_provider = (
            meta.get("embedding_provider")
            or meta.get("provider")
            or self.ingest_default_provider
            or self.default_provider
        )
        requested_mode = meta.get("embedding_mode") or meta.get("jina_mode")
        profile = self.resolve_embedding_profile(
            provider=str(requested_provider) if requested_provider else None,
            mode=str(requested_mode) if requested_mode else None,
        )
        fallback_provider = self._resolve_ingest_fallback_provider(
            str(requested_provider) if requested_provider else None
        )
        profile["fallback_provider"] = fallback_provider
        profile["fallback_on_technical_error"] = bool(
            getattr(settings, "INGEST_EMBED_FALLBACK_ON_TECHNICAL_ERROR", True)
        )
        return profile

    def ensure_profile_compatibility(
        self,
        *,
        index_profile: Dict[str, Any] | None,
        query_provider: Optional[str] = None,
        query_mode: Optional[str] = None,
    ) -> bool:
        if not index_profile:
            return True
        query_profile = self.resolve_embedding_profile(provider=query_provider, mode=query_mode)
        expected_provider = str(index_profile.get("provider") or "").strip().lower()
        expected_model = str(index_profile.get("model") or "").strip().lower()
        expected_dims = int(index_profile.get("dimensions") or 0)
        actual_provider = str(query_profile.get("provider") or "").strip().lower()
        actual_model = str(query_profile.get("model") or "").strip().lower()
        actual_dims = int(query_profile.get("dimensions") or 0)
        return (
            expected_provider == actual_provider
            and expected_model == actual_model
            and expected_dims == actual_dims
        )

    async def close(self) -> None:
        providers = [self.jina_cloud_provider, self.cohere_cloud_provider, self.local_provider]
        for provider in providers:
            close_fn = getattr(provider, "close", None)
            if callable(close_fn):
                try:
                    result = close_fn()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "embedding_provider_close_failed",
                        provider=getattr(provider, "provider_name", provider.__class__.__name__),
                        error=str(exc),
                    )

    @track_span(name="span:embedding_generation")
    async def embed_texts(
        self,
        texts: List[str],
        task: str = "retrieval.passage",
        mode: Optional[str] = None,
        provider: Optional[str] = None,
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
        runtime_provider = self._get_provider(mode=mode, provider=provider)
        requested_provider_name = self._resolve_provider_name(provider)
        try:
            async with self._embedding_semaphore:
                embeddings = await runtime_provider.embed(unique_texts, task=task)

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
            fallback_provider = self._resolve_ingest_fallback_provider(provider)
            should_try_fallback = (
                task != "retrieval.query"
                and fallback_provider is not None
                and requested_provider_name == "cohere"
                and self._is_technical_provider_error(e)
            )
            if should_try_fallback:
                logger.warning(
                    "embedding_provider_fallback_attempt",
                    primary_provider=requested_provider_name,
                    fallback_provider=fallback_provider,
                    error=str(e),
                    texts_count=len(texts),
                    task=task,
                )
                fallback_runtime_provider = self._get_provider(
                    mode=mode, provider=fallback_provider
                )
                async with self._embedding_semaphore:
                    embeddings = await fallback_runtime_provider.embed(unique_texts, task=task)
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
                logger.warning(
                    "embedding_provider_fallback_succeeded",
                    primary_provider=requested_provider_name,
                    applied_provider=fallback_provider,
                    texts_count=len(texts),
                    task=task,
                )
                return cast(List[List[float]], final_embeddings)

            logger.error(
                "embedding_generation_failed", error=str(e), texts_count=len(texts), exc_info=True
            )
            raise e

    @track_span(name="span:late_chunking")
    async def chunk_and_encode(
        self,
        text: str,
        mode: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        runtime_provider = self._get_provider(mode=mode, provider=provider)
        requested_provider_name = self._resolve_provider_name(provider)
        try:
            async with self._embedding_semaphore:
                return await runtime_provider.chunk_and_encode(text)
        except Exception as e:
            fallback_provider = self._resolve_ingest_fallback_provider(provider)
            should_try_fallback = (
                fallback_provider is not None
                and requested_provider_name == "cohere"
                and self._is_technical_provider_error(e)
            )
            if should_try_fallback:
                logger.warning(
                    "chunk_and_encode_provider_fallback_attempt",
                    primary_provider=requested_provider_name,
                    fallback_provider=fallback_provider,
                    error=str(e),
                )
                fallback_runtime_provider = self._get_provider(
                    mode=mode, provider=fallback_provider
                )
                async with self._embedding_semaphore:
                    chunks = await fallback_runtime_provider.chunk_and_encode(text)
                logger.warning(
                    "chunk_and_encode_provider_fallback_succeeded",
                    primary_provider=requested_provider_name,
                    applied_provider=fallback_provider,
                )
                return chunks
            raise e


# Alias for better nomenclature
EmbeddingService = JinaEmbeddingService
