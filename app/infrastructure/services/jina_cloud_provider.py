import structlog
import aiohttp
import asyncio
import textwrap
import numpy as np
from typing import List, Dict, Any, Optional, cast
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.core.ai_models import AIModelConfig
from app.core.settings import settings

logger = structlog.get_logger(__name__)


class JinaCloudProvider(IEmbeddingProvider):
    """
    Implementation of Jina embeddings using the Cloud API.
    Uses a shared aiohttp.ClientSession for connection reuse.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = AIModelConfig.JINA_BASE_URL
        self._model_name = self._normalize_cloud_model_name(AIModelConfig.JINA_MODEL_NAME)
        self._dimensions = AIModelConfig.JINA_EMBEDDING_DIMENSIONS
        self._session: Optional[aiohttp.ClientSession] = None
        self._post_semaphore = asyncio.Semaphore(
            max(1, int(settings.JINA_REQUEST_MAX_PARALLEL or 2))
        )
        self._rate_limited_until: float = 0.0
        self._rate_limit_lock = asyncio.Lock()
        if self._model_name != AIModelConfig.JINA_MODEL_NAME:
            logger.info(
                "jina_cloud_model_name_normalized",
                configured_model=AIModelConfig.JINA_MODEL_NAME,
                cloud_model=self._model_name,
            )
        key_suffix = str(self.api_key or "")[-4:] if self.api_key else "none"
        logger.info(
            "jina_cloud_provider_initialized",
            model=self._model_name,
            key_suffix=key_suffix,
            max_parallel_requests=max(1, int(settings.JINA_REQUEST_MAX_PARALLEL or 2)),
        )

    @staticmethod
    def _normalize_cloud_model_name(model_name: str) -> str:
        """Map HF-style model ids to Jina Cloud API tags."""
        value = str(model_name or "").strip()
        if not value:
            return "jina-embeddings-v3"
        if "/" in value:
            value = value.rsplit("/", 1)[-1]
        return value

    async def _get_session(self) -> aiohttp.ClientSession:
        """Returns a shared session, creating one if needed."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
            )
        return self._session

    async def close(self):
        """Closes the shared session. Call on shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def provider_name(self) -> str:
        return "jina"

    @property
    def model_name(self) -> str:
        return str(self._model_name)

    @property
    def embedding_dimensions(self) -> int:
        return int(self._dimensions)

    def _safe_split_text(self, text: str, max_chars: int = 15000) -> List[str]:
        """
        Divide textos gigantes en partes seguras (aprox < 4000 tokens)
        asegurando que NINGUNA parte exceda el límite físico.
        """
        if len(text) <= max_chars:
            return [text]

        # Primero intentamos por párrafos o espacios
        chunks = textwrap.wrap(
            text, width=max_chars, break_long_words=False, replace_whitespace=False
        )

        # Verificación de seguridad: si alguna parte sigue siendo muy grande (sin espacios), forzamos corte
        final_chunks = []
        for c in chunks:
            if len(c) > max_chars + 1000:  # Margen pequeño
                # Corte por fuerza bruta de caracteres
                for i in range(0, len(c), max_chars):
                    final_chunks.append(c[i : i + max_chars])
            else:
                final_chunks.append(c)
        return final_chunks

    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        if not texts:
            return []

        # 1. Pre-procesamiento para detectar Jumbos ilegales
        processed_texts = []
        indices_map = []  # Para reconstruir el orden si se divide un texto

        for idx, text in enumerate(texts):
            # Si el texto es > ~30k caracteres, Jina va a fallar (8192 tokens)
            splits = self._safe_split_text(text)
            for s in splits:
                processed_texts.append(s)
                indices_map.append(idx)

        # 2. Batching (Reducido para evitar rate limits)
        batch_size = max(1, min(32, int(settings.JINA_BATCH_SIZE or 8)))
        batch_delay = float(settings.JINA_BATCH_RATE_LIMIT_DELAY_SECONDS or 1.0)
        all_embeddings = []

        session = await self._get_session()

        for i in range(0, len(processed_texts), batch_size):
            batch = processed_texts[i : i + batch_size]

            # Rate limiting entre batches (excepto el primero)
            if i > 0 and batch_delay > 0:
                await asyncio.sleep(batch_delay)

            # Payload optimizado para Jina v3
            data = {
                "model": self._model_name,
                "task": task,
                "dimensions": self._dimensions,
                "late_chunking": True,  # ACTIVAR LATE CHUNKING
                "embedding_type": "float",
                "truncate": True,  # Seguridad extra: truncar si algo se escapa
                "input": batch,
            }

            result = await self._post_with_retry(session=session, data=data)
            data_raw = result.get("data") if isinstance(result, dict) else []
            data_list = data_raw if isinstance(data_raw, list) else []
            entries: List[Dict[str, Any]] = [
                cast(Dict[str, Any], row) for row in data_list if isinstance(row, dict)
            ]
            sorted_data = sorted(entries, key=lambda x: int(x.get("index", 0)))
            all_embeddings.extend([item["embedding"] for item in sorted_data])

        # 3. Reconstrucción (Mean Pooling para textos divididos)
        # Si tuviste que dividir un texto gigante, promedias sus vectores
        final_embeddings = []

        # Agrupar embeddings por su índice original
        temp_map = {}
        for original_idx, emb in zip(indices_map, all_embeddings):
            if original_idx not in temp_map:
                temp_map[original_idx] = []
            temp_map[original_idx].append(emb)

        # Reordenar y promediar
        for i in range(len(texts)):
            vectors = temp_map.get(i, [])
            if not vectors:
                # Should not happen if API works, but safety fallback
                final_embeddings.append([0.0] * self._dimensions)
            elif len(vectors) == 1:
                final_embeddings.append(vectors[0])
            else:
                # Promedio simple de vectores (Mean Pooling) para reconstruir el Jumbo
                avg_vec = np.mean(vectors, axis=0).tolist()
                final_embeddings.append(avg_vec)

        return final_embeddings

    async def chunk_and_encode(self, text: str) -> List[Dict[str, Any]]:
        """
        Cloud version using Jina's native late_chunking=True.
        Sends full text and receives chunked embeddings.
        If late chunking fails, falls back to simple splitting.
        """
        chunks = self._split_text_simple(text, limit=1000)  # Pre-split locally to allow array input
        data = {
            "model": self._model_name,
            "input": chunks,
            "dimensions": self._dimensions,
            "late_chunking": True,
        }

        session = await self._get_session()

        try:
            async with session.post(self.base_url, json=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    embeddings_data = result.get("data", [])
                    if embeddings_data:
                        # Late chunking returns one embedding per chunk
                        # Late chunking with array input returns one embedding per input chunk,
                        # but informed by the global context of the array.
                        entries: List[Dict[str, Any]] = [
                            cast(Dict[str, Any], row)
                            for row in embeddings_data
                            if isinstance(row, dict)
                        ]
                        sorted_d = sorted(entries, key=lambda x: int(x.get("index", 0)))
                        return self._map_late_chunks(chunks, sorted_d)

                # Fallback to simple splitting if late chunking doesn't return expected format
                logger.warning(
                    "late_chunking_cloud_fallback", status=resp.status if resp else "no_response"
                )
        except Exception as e:
            logger.warning("late_chunking_cloud_error_fallback", error=str(e))

        # Fallback: simple splitting + batch embedding
        return await self._fallback_chunk_and_encode(text, session)

    def _map_late_chunks(self, chunks: List[str], embeddings_data: list) -> List[Dict[str, Any]]:
        """
        Maps Jina late chunking array response back to text with offsets.
        """
        results = []
        offset = 0

        # Jina returns 'index' matching the input array order
        for i, content in enumerate(chunks):
            # Find corresponding embedding by index
            # If Jina combined/split (unlikely with array input + late_chunking), we safe-guard
            emb_item = next((x for x in embeddings_data if x["index"] == i), None)
            embedding = emb_item["embedding"] if emb_item else [0.0] * self._dimensions

            results.append(
                {
                    "content": content,
                    "embedding": embedding,
                    "char_start": offset,
                    "char_end": offset + len(content),
                }
            )
            offset += len(content)
        return results

    async def _fallback_chunk_and_encode(
        self, text: str, session: aiohttp.ClientSession
    ) -> List[Dict[str, Any]]:
        """Fallback: simple split + batch embed using shared session."""
        chunks = self._split_text_simple(text)
        embeddings = []

        batch_size = 32
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            try:
                payload = await self._post_with_retry(
                    session=session,
                    data={
                        "model": self._model_name,
                        "input": batch,
                        "dimensions": self._dimensions,
                    },
                )
                data_raw = payload.get("data") if isinstance(payload, dict) else []
                data_list = data_raw if isinstance(data_raw, list) else []
                entries: List[Dict[str, Any]] = [
                    cast(Dict[str, Any], row) for row in data_list if isinstance(row, dict)
                ]
                sorted_d = sorted(entries, key=lambda x: int(x.get("index", 0)))
                embeddings.extend([x["embedding"] for x in sorted_d])
            except Exception as exc:
                logger.error("cloud_embedding_fallback_failed", error=str(exc))
                embeddings.extend([[0.0] * self._dimensions] * len(batch))

        results = []
        offset = 0
        for c, emb in zip(chunks, embeddings):
            results.append(
                {"content": c, "embedding": emb, "char_start": offset, "char_end": offset + len(c)}
            )
            offset += len(c)

        return results

    async def _post_with_retry(
        self,
        *,
        session: aiohttp.ClientSession,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        attempts = max(1, int(settings.JINA_EMBED_RETRY_MAX_ATTEMPTS or 5))
        base_delay = float(settings.JINA_EMBED_RETRY_BASE_DELAY_SECONDS or 0.4)
        max_delay = float(settings.JINA_EMBED_RETRY_MAX_DELAY_SECONDS or 10.0)
        backoff_429_multiplier = float(settings.JINA_EMBED_RETRY_429_BACKOFF_MULTIPLIER or 3.0)

        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._wait_for_rate_limit_window()
                async with self._post_semaphore:
                    async with session.post(self.base_url, json=data) as response:
                        if response.status == 200:
                            result = await response.json()
                            if isinstance(result, dict):
                                return result
                            raise Exception("Jina API Error: malformed JSON response")

                        text_resp = await response.text()
                        is_rate_limit = response.status == 429
                        retryable = response.status in {429, 500, 502, 503, 504}

                        if not retryable or attempt >= attempts:
                            logger.error(f"Jina API Error {response.status}: {text_resp}")
                            raise Exception(f"Jina API Error: {text_resp}")

                        # Backoff más agresivo para rate limits (429)
                        if is_rate_limit:
                            retry_after_header = response.headers.get("Retry-After")
                            try:
                                retry_after_s = (
                                    float(retry_after_header) if retry_after_header else 0.0
                                )
                            except (TypeError, ValueError):
                                retry_after_s = 0.0
                            delay = min(
                                max_delay,
                                max(
                                    retry_after_s,
                                    base_delay * (backoff_429_multiplier ** (attempt - 1)),
                                ),
                            )
                            await self._register_rate_limit_delay(delay)
                            logger.warning(
                                "jina_embed_rate_limit_retry",
                                status=response.status,
                                attempt=attempt,
                                delay_seconds=delay,
                                batch_size=len(data.get("input", [])),
                            )
                        else:
                            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                            logger.warning(
                                "jina_embed_retry",
                                status=response.status,
                                attempt=attempt,
                                delay_seconds=delay,
                            )
                await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise Exception("Jina API Error: retry exhausted")

    async def _register_rate_limit_delay(self, delay_seconds: float) -> None:
        cooldown_floor = max(0.0, float(settings.JINA_RATE_LIMIT_COOLDOWN_SECONDS or 0.0))
        target_delay = max(float(delay_seconds or 0.0), cooldown_floor)
        if target_delay <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._rate_limit_lock:
            self._rate_limited_until = max(self._rate_limited_until, loop.time() + target_delay)

    async def _wait_for_rate_limit_window(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            async with self._rate_limit_lock:
                remaining = self._rate_limited_until - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    def _split_text_simple(self, text: str, limit: int = 1000) -> List[str]:
        parts = []
        while len(text) > limit:
            split_at = text.rfind("\n", 0, limit)
            if split_at == -1:
                split_at = limit
            parts.append(text[:split_at])
            text = text[split_at:].strip()
        if text:
            parts.append(text)
        return parts
