import structlog
import threading
import asyncio
from typing import List, Dict, Any, Optional
from app.core.observability.metrics import track_span
from app.core.settings import settings
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.infrastructure.services.jina_local_provider import JinaLocalProvider
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
            
            # Providers
            self.local_provider = JinaLocalProvider()
            self.cloud_provider = JinaCloudProvider(api_key=settings.JINA_API_KEY) if settings.JINA_API_KEY else None
            
            # Default behavior from env
            self.default_cloud = settings.JINA_MODE == "CLOUD"
            if self.default_cloud and not self.cloud_provider:
                 logger.warning("⚠️ JINA_MODE=CLOUD but JINA_API_KEY not found. Fallback to LOCAL.")
                 self.default_cloud = False
            
            # Caching for query embeddings
            self._cache = {}
            self._cache_max_size = 1000
            self._cache_lock = threading.Lock()

            # Throughput controls
            self.embedding_concurrency = max(1, int(getattr(settings, "EMBEDDING_CONCURRENCY", 5)))
            self._embedding_semaphore = asyncio.Semaphore(self.embedding_concurrency)
            
            self._initialized = True

    @classmethod
    def get_instance(cls):
        return cls() # Singleton


    def _get_provider(self, mode: Optional[str] = None) -> IEmbeddingProvider:
        use_cloud = self.default_cloud
        if mode:
            use_cloud = (mode == "CLOUD")
            
        if use_cloud and self.cloud_provider:
            return self.cloud_provider
        
        return self.local_provider

    @track_span(name="span:embedding_generation")
    async def embed_texts(self, texts: List[str], task: str = "retrieval.passage", mode: Optional[str] = None) -> List[List[float]]:
        if not texts: return []
        
        # Cache logic
        is_query_task = task == "retrieval.query"
        final_embeddings = [None] * len(texts)
        missing_indices = []
        missing_texts = []
        
        if is_query_task:
            with self._cache_lock:
                for i, t in enumerate(texts):
                    cache_key = (t, task)
                    if cache_key in self._cache:
                        final_embeddings[i] = self._cache[cache_key]
                        # Move to end (LRU)
                        self._cache[cache_key] = self._cache.pop(cache_key)
                    else:
                        missing_indices.append(i)
                        missing_texts.append(t)
        else:
            missing_indices = list(range(len(texts)))
            missing_texts = texts

        if not missing_texts:
            return final_embeddings 

        # Delegate to provider
        provider = self._get_provider(mode)
        try:
            async with self._embedding_semaphore:
                embeddings = await provider.embed(missing_texts, task=task)
            
            # Update cache and merge results
            for i, emb in zip(missing_indices, embeddings):
                final_embeddings[i] = emb
                if is_query_task:
                    with self._cache_lock:
                        cache_key = (texts[i], task)
                        self._cache[cache_key] = emb
                        if len(self._cache) > self._cache_max_size:
                            self._cache.pop(next(iter(self._cache)))
            
            return final_embeddings
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
