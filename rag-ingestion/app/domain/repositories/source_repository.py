from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.schemas import SourceDocument

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
    async def create_source_document(self, doc: 'SourceDocument') -> 'SourceDocument':
        pass
