from typing import Protocol, Optional
from app.schemas.ingestion import IngestionMetadata
from app.domain.types.ingestion_status import IngestionStatus

class ITaxonomyManager(Protocol):
    """
    Interface for Taxonomy Manager.
    Defines the contract for registering documents and resolving strategies.
    """
    async def register_document(self, filename: str, metadata: IngestionMetadata, course_id: str = None, initial_status: IngestionStatus = IngestionStatus.PENDING_INGESTION) -> str:
        ...

    async def resolve_strategy_slug(self, type_id: str) -> str:
        ...
