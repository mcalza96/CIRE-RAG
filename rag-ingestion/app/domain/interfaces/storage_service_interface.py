from abc import ABC, abstractmethod
from typing import Optional
from app.domain.models.ingestion_source import IngestionSource

class IStorageService(ABC):
    """
    Interface for storage operations.
    """
    @abstractmethod
    async def download_to_temp(
        self,
        storage_path: str,
        filename: str,
        bucket_name: Optional[str] = None,
    ) -> IngestionSource:
        """
        Downloads a file and returns an IngestionSource object.
        """
        pass
