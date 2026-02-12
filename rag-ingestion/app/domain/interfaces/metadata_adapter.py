from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.schemas.ingestion import IngestionMetadata

class IMetadataAdapter(ABC):
    """
    Interface for mapping raw database records to IngestionMetadata.
    """
    
    @abstractmethod
    def map_to_domain(
        self,
        record: Dict[str, Any], 
        metadata: Dict[str, Any], 
        source_id: str, 
        filename: str, 
        is_global: bool, 
        institution_id: Optional[str]
    ) -> IngestionMetadata:
        """
        Maps raw input data to a clean IngestionMetadata object.
        """
        pass
