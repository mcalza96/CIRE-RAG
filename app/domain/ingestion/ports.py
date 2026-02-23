from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.domain.schemas.knowledge_schemas import RAGSearchResult, RetrievalIntent
    from .graph.graph_extractor import ChunkGraphExtraction
    from app.domain.schemas.ingestion_schemas import IngestionMetadata
    from app.domain.ingestion.entities import IngestionSource
    from app.domain.schemas import SourceDocument
    from app.domain.schemas.raptor_schemas import SummaryNode

class IContentRepository(ABC):
    @abstractmethod
    async def save_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        pass
    @abstractmethod
    async def delete_chunks_by_source_id(self, source_id: str) -> None:
        pass
    @abstractmethod
    async def get_chunk_by_id(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        pass
    @abstractmethod
    async def get_first_chunk_by_page(self, source_id: str, page: int) -> Optional[Dict[str, Any]]:
        pass
    @abstractmethod
    async def get_first_chunk(self, source_id: str) -> Optional[Dict[str, Any]]:
        pass
    @abstractmethod
    async def update_chunk_content(self, chunk_id: str, content: str) -> None:
        pass
    @abstractmethod
    async def get_chunks_by_source_id(self, source_id: str) -> List[Dict[str, Any]]:
        pass

class IVisualCacheRepository(ABC):
    @abstractmethod
    async def get_cached_extractions(
        self,
        hashes: List[str],
        content_types: List[str],
        provider: str,
        model_name: str,
        prompt_version: str,
        schema_version: str,
    ) -> List[Dict[str, Any]]:
        pass

class IRaptorRepository(ABC):
    @abstractmethod
    async def save_summary_node(self, node: Any) -> None:
        pass
    @abstractmethod
    async def save_summary_nodes(self, nodes: List[Any]) -> None:
        pass

class ISourceRepository(ABC):
    @abstractmethod
    async def update_status(self, doc_id: str, status: str, error_message: Optional[str] = None) -> None:
        pass
    @abstractmethod
    async def update_metadata(self, doc_id: str, metadata: Dict[str, Any]) -> None:
        pass
    @abstractmethod
    async def update_status_and_metadata(self, doc_id: str, status: str, metadata: Dict[str, Any]) -> None:
        pass
    @abstractmethod
    async def log_event(
        self,
        doc_id: str,
        message: str,
        status: str = "INFO",
        node_type: str = "SYSTEM",
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass
    @abstractmethod
    async def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        pass
    @abstractmethod
    async def create_source_document(self, doc: Any) -> Any:
        pass

class IEmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: List[str], task: str = "retrieval.passage") -> List[List[float]]:
        pass
    @abstractmethod
    async def chunk_and_encode(self, text: str) -> List[Dict[str, Any]]:
        pass
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass
    @property
    @abstractmethod
    def model_name(self) -> str:
        pass
    @property
    @abstractmethod
    def embedding_dimensions(self) -> int:
        pass
    def profile(self) -> Dict[str, Any]:
        return {
            "provider": str(self.provider_name),
            "model": str(self.model_name),
            "dimensions": int(self.embedding_dimensions),
        }
class IGraphRepository(ABC):
    @abstractmethod
    async def upsert_knowledge_subgraph(
        self,
        extraction: "ChunkGraphExtraction",
        tenant_id: UUID,
        chunk_id: Optional[UUID] = None,
        entity_embeddings: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def upsert_document_structure(
        self,
        tenant_id: UUID,
        source_document_id: UUID,
        toc_entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def link_chunks_to_structure(
        self,
        tenant_id: UUID,
        source_document_id: UUID,
        chunks: List[Any],
    ) -> Dict[str, Any]:
        pass
