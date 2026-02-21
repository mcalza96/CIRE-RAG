import structlog
import re
import numpy as np
import threading
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from app.domain.interfaces.embedding_provider import IEmbeddingProvider
from app.ai.contracts import AIModelConfig

logger = structlog.get_logger(__name__)


class JinaLocalProvider(IEmbeddingProvider):
    """
    Implementation of Jina embeddings using local Transformers model.
    """

    _model_name = AIModelConfig.JINA_MODEL_NAME

    @property
    def provider_name(self) -> str:
        return "jina"

    @property
    def model_name(self) -> str:
        return str(self._model_name)

    @property
    def embedding_dimensions(self) -> int:
        return int(AIModelConfig.JINA_EMBEDDING_DIMENSIONS)

    def __init__(self):
        self.device = "cpu"
        self.model = None
        self.tokenizer = None
        self._lock = threading.Lock()
        self._initialized = False
        self._torch = None

    def _import_runtime(self):
        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoTokenizer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Local embedding runtime is not installed. "
                "Install requirements-local.txt or run with JINA_MODE=CLOUD."
            ) from exc
        return torch, AutoModel, AutoTokenizer

    def _get_device(self, torch_module) -> str:
        if torch_module.cuda.is_available():
            return "cuda"
        if torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _load_model(self):
        with self._lock:
            if self.model is not None:
                return

            torch_module, AutoModel, AutoTokenizer = self._import_runtime()
            self.device = self._get_device(torch_module)
            self._torch = torch_module

            logger.info(
                f"⏳ [JinaLocalProvider] Loading model {self._model_name} on {self.device}..."
            )
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    self._model_name, trust_remote_code=True
                )
                self.model = AutoModel.from_pretrained(
                    self._model_name,
                    trust_remote_code=True,
                    dtype=torch_module.float16 if self.device != "cpu" else torch_module.float32,
                )
                self.model.to(self.device)
                self.model.eval()
                logger.info("✅ [JinaLocalProvider] Model loaded successfully.")
                self._initialized = True
            except Exception as e:
                logger.error(f"❌ [JinaLocalProvider] Failed to load model: {e}")
                raise e

    async def _ensure_model_loaded(self):
        if not self._initialized:
            await asyncio.to_thread(self._load_model)

    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        if not texts:
            return []

        await self._ensure_model_loaded()

        logger.info(
            f"🔢 [JinaLocalProvider] Generating embeddings for {len(texts)} texts (task: {task})..."
        )
        embeddings = await asyncio.to_thread(
            self.model.encode, texts, task=task, batch_size=4, show_progress_bar=False
        )

        if hasattr(embeddings, "tolist"):
            return embeddings.tolist()
        return [list(e) for e in embeddings]

    async def chunk_and_encode(self, text: str) -> List[Dict[str, Any]]:
        await self._ensure_model_loaded()

        max_length = 8192
        spans = self._identify_spans(text)
        if not spans:
            return []

        tokens = self.tokenizer.encode(text, add_special_tokens=False)

        if len(tokens) <= max_length - 2:
            return self._process_window(text, spans, 0, len(text))

        # Sliding Window Logic
        logger.info(f"Long text sliding window started: {len(tokens)} tokens")

        all_chunks = []
        char_window_limit = (max_length - 500) * 4

        current_char = 0
        text_len = len(text)
        seen_spans = set()

        while current_char < text_len:
            window_end = min(current_char + char_window_limit, text_len)
            window_spans = [
                s
                for s in spans
                if s[0] >= current_char and s[0] < window_end and s not in seen_spans
            ]

            if not window_spans:
                next_spans = [s for s in spans if s[0] >= current_char]
                if next_spans:
                    current_char = next_spans[0][0]
                    continue
                else:
                    break

            actual_window_end = min(window_spans[-1][1], text_len)
            window_text = text[current_char:actual_window_end]
            window_tokens = self.tokenizer.encode(window_text, add_special_tokens=False)

            while len(window_tokens) > max_length - 2 and len(window_spans) > 1:
                window_spans.pop()
                actual_window_end = window_spans[-1][1]
                window_text = text[current_char:actual_window_end]
                window_tokens = self.tokenizer.encode(window_text, add_special_tokens=False)

            chunks = self._process_window(text, window_spans, current_char, actual_window_end)
            all_chunks.extend(chunks)
            for s in window_spans:
                seen_spans.add(s)
            current_char = actual_window_end

        return all_chunks

    def _process_window(
        self,
        full_text: str,
        window_spans: List[Tuple[int, int]],
        window_start: int,
        window_end: int,
    ) -> List[Dict[str, Any]]:
        if self._torch is None:
            raise RuntimeError("Torch runtime not initialized before window processing")

        window_text = full_text[window_start:window_end]

        inputs = self.tokenizer(
            window_text,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=8192,
            return_offsets_mapping=True,
        )

        model_inputs = {k: v.to(self.device) for k, v in inputs.items() if k != "offset_mapping"}
        offset_mapping = inputs["offset_mapping"][0].cpu().numpy()

        with self._torch.no_grad():
            outputs = self.model(**model_inputs)
            token_embeddings = outputs.last_hidden_state[0]

        chunks_data = []
        for start_char_abs, end_char_abs in window_spans:
            start_char_rel = start_char_abs - window_start
            end_char_rel = end_char_abs - window_start

            chunk_text = full_text[start_char_abs:end_char_abs].strip()
            if not chunk_text:
                continue

            token_indices = np.where(
                (offset_mapping[:, 1] > start_char_rel) & (offset_mapping[:, 0] < end_char_rel)
            )[0]
            if len(token_indices) == 0:
                continue

            start_token = token_indices[0]
            end_token = token_indices[-1] + 1
            pooled_vector = (
                token_embeddings[start_token:end_token].mean(dim=0).cpu().numpy().tolist()
            )

            chunks_data.append(
                {
                    "content": chunk_text,
                    "embedding": pooled_vector,
                    "char_start": start_char_abs,
                    "char_end": end_char_abs,
                }
            )

        return chunks_data

    def _identify_spans(self, text: str) -> List[Tuple[int, int]]:
        spans = []
        matches = list(re.finditer(r"\n\s*\n", text))
        start = 0
        for match in matches:
            end = match.start()
            if end > start:
                spans.append((start, end))
            start = match.end()
        if start < len(text):
            spans.append((start, len(text)))
        return spans
