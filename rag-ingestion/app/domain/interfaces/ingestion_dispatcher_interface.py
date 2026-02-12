from abc import ABC, abstractmethod
from app.schemas.ingestion import IngestionMetadata
from app.domain.models.ingestion_source import IngestionSource
from app.workflows.ingestion.strategies import IngestionResult

class IIngestionDispatcher(ABC):
    """
    Interface for the Ingestion Dispatcher.
    """
    @abstractmethod
    async def dispatch(
        self, 
        source: IngestionSource, 
        metadata: IngestionMetadata, 
        strategy_key: str, 
        source_id: str
    ) -> IngestionResult:
        """
        Dispatches a document to the appropriate strategy.
        """
        pass
