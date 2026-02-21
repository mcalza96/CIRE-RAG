from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
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
