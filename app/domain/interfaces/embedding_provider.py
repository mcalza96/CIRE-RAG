from abc import ABC, abstractmethod
from typing import List, Dict, Any


class IEmbeddingProvider(ABC):
    """
    Interface for embedding providers.
    Encapsulates both standard text embedding and semantic chunking (late chunking).
    """

    @abstractmethod
    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        """
        Generates 1024d embeddings for a list of texts.
        """
        pass

    @abstractmethod
    async def chunk_and_encode(self, text: str) -> List[Dict[str, Any]]:
        """
        Performs semantic chunking (Late Chunking) and returns chunks with embeddings and offsets.
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier (e.g. 'jina', 'cohere')."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Configured embedding model identifier."""
        pass

    @property
    @abstractmethod
    def embedding_dimensions(self) -> int:
        """Output vector dimensions for this provider/model."""
        pass

    def profile(self) -> Dict[str, Any]:
        """Provider-agnostic embedding profile metadata for traceability."""
        return {
            "provider": str(self.provider_name),
            "model": str(self.model_name),
            "dimensions": int(self.embedding_dimensions),
        }
