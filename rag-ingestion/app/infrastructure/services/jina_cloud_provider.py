import structlog
import aiohttp
import textwrap
import numpy as np
from typing import List, Dict, Any, Optional
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.core.ai_models import AIModelConfig

logger = structlog.get_logger(__name__)

class JinaCloudProvider(IEmbeddingProvider):
    """
    Implementation of Jina embeddings using the Cloud API.
    Uses a shared aiohttp.ClientSession for connection reuse.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = AIModelConfig.JINA_BASE_URL
        self.model_name = self._normalize_cloud_model_name(AIModelConfig.JINA_MODEL_NAME)
        self.dimensions = AIModelConfig.JINA_EMBEDDING_DIMENSIONS
        self._session: Optional[aiohttp.ClientSession] = None
        if self.model_name != AIModelConfig.JINA_MODEL_NAME:
            logger.info(
                "jina_cloud_model_name_normalized",
                configured_model=AIModelConfig.JINA_MODEL_NAME,
                cloud_model=self.model_name,
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

    def _safe_split_text(self, text: str, max_chars: int = 15000) -> List[str]:
        """
        Divide textos gigantes en partes seguras (aprox < 4000 tokens) 
        asegurando que NINGUNA parte exceda el límite físico.
        """
        if len(text) <= max_chars:
            return [text]
        
        # Primero intentamos por párrafos o espacios
        chunks = textwrap.wrap(text, width=max_chars, break_long_words=False, replace_whitespace=False)
        
        # Verificación de seguridad: si alguna parte sigue siendo muy grande (sin espacios), forzamos corte
        final_chunks = []
        for c in chunks:
            if len(c) > max_chars + 1000: # Margen pequeño
                # Corte por fuerza bruta de caracteres
                for i in range(0, len(c), max_chars):
                    final_chunks.append(c[i:i + max_chars])
            else:
                final_chunks.append(c)
        return final_chunks

    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        if not texts:
            return []

        # 1. Pre-procesamiento para detectar Jumbos ilegales
        processed_texts = []
        indices_map = [] # Para reconstruir el orden si se divide un texto
        
        for idx, text in enumerate(texts):
            # Si el texto es > ~30k caracteres, Jina va a fallar (8192 tokens)
            splits = self._safe_split_text(text)
            for s in splits:
                processed_texts.append(s)
                indices_map.append(idx)

        # 2. Batching (Jina recomienda batches de ~32 a 64 items)
        batch_size = 32
        all_embeddings = []

        session = await self._get_session()
        
        for i in range(0, len(processed_texts), batch_size):
            batch = processed_texts[i:i+batch_size]
            
            # Payload optimizado para Jina v3
            data = {
                "model": self.model_name,
                "task": task,
                "dimensions": self.dimensions,
                "late_chunking": True, # ACTIVAR LATE CHUNKING
                "embedding_type": "float",
                "truncate": True, # Seguridad extra: truncar si algo se escapa
                "input": batch
            }

            async with session.post(self.base_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    # Ordenar por índice devuelto por Jina para asegurar consistencia
                    sorted_data = sorted(result["data"], key=lambda x: x["index"])
                    all_embeddings.extend([item["embedding"] for item in sorted_data])
                else:
                    text_resp = await response.text()
                    logger.error(f"Jina API Error {response.status}: {text_resp}")
                    # Estrategia Fail-open o Raise según prefieras
                    raise Exception(f"Jina API Error: {text_resp}")

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
                final_embeddings.append([0.0] * self.dimensions)
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
        chunks = self._split_text_simple(text, limit=1000) # Pre-split locally to allow array input
        data = {
            "model": self.model_name,
            "input": chunks,
            "dimensions": self.dimensions,
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
                        sorted_d = sorted(embeddings_data, key=lambda x: x["index"])
                        return self._map_late_chunks(chunks, sorted_d)
                
                # Fallback to simple splitting if late chunking doesn't return expected format
                logger.warning("late_chunking_cloud_fallback", status=resp.status if resp else "no_response")
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
            embedding = emb_item["embedding"] if emb_item else [0.0] * self.dimensions
            
            results.append({
                "content": content,
                "embedding": embedding,
                "char_start": offset,
                "char_end": offset + len(content),
            })
            offset += len(content)
        return results

    async def _fallback_chunk_and_encode(self, text: str, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        """Fallback: simple split + batch embed using shared session."""
        chunks = self._split_text_simple(text)
        embeddings = []
        
        batch_size = 32
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            async with session.post(self.base_url, json={
                "model": self.model_name, 
                "input": batch,
                "dimensions": self.dimensions
            }) as resp:
                if resp.status == 200:
                    data = (await resp.json())["data"]
                    sorted_d = sorted(data, key=lambda x: x["index"])
                    embeddings.extend([x["embedding"] for x in sorted_d])
                else:
                    err_text = await resp.text()
                    logger.error(f"Cloud embedding failed: {err_text}")
                    embeddings.extend([[0.0] * self.dimensions] * len(batch))
        
        results = []
        offset = 0
        for c, emb in zip(chunks, embeddings):
            results.append({
                "content": c,
                "embedding": emb,
                "char_start": offset,
                "char_end": offset + len(c)
            })
            offset += len(c)
        
        return results

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
