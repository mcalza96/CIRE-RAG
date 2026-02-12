import structlog
from typing import Dict, Any, Type, Optional

from app.schemas.ingestion import IngestionMetadata, IngestionType
from app.workflows.ingestion.strategies import IngestionResult, IngestionStrategy
from app.domain.models.ingestion_source import IngestionSource
from app.domain.interfaces.ingestion_dispatcher_interface import IIngestionDispatcher
from app.core.registry import registry


logger = structlog.get_logger(__name__)

class IngestionDispatcher(IIngestionDispatcher):
    """
    Traffic controller for file ingestion.
    Decides which strategy to use (OCP). Pure transformation (SRP).
    """
    
    def __init__(self, repository: Optional[Any] = None):
        self._strategy_instances: Dict[str, IngestionStrategy] = {}
        self._repository = repository

    async def dispatch(self, source: IngestionSource, metadata: IngestionMetadata, strategy_key: str, source_id: str) -> IngestionResult:
        """
        Main entry point.
        1. Resolve Strategy from Registry.
        2. Execute Strategy (Pure transformation).
        """
        filename = source.get_filename()
        logger.info(f"[Dispatcher] Dispatching file: {filename} with strategy {strategy_key}")
        
        if not source_id:
            raise ValueError("[Dispatcher] source_id is required.")

        # Resolve Strategy using registry
        strategy = self._get_strategy_instance(strategy_key)
        
        if not strategy:
            available = registry.list_strategies()
            raise ValueError(f"No strategy found for key: {strategy_key}. Available: {available}")
            
        # Execute Strategy (Pure)
        try:
            # Ensure strategy receives the source ID for its metadata
            metadata_with_id = metadata.copy(update={"source_id": source_id})
            result = await strategy.process(source, metadata_with_id)
            
            # Override source_id just in case
            result.source_id = source_id 
            return result
        except Exception as e:
            logger.error(f"[Dispatcher] Strategy execution failed: {e}")
            raise e

    def _get_strategy_instance(self, strategy_key: str) -> Optional[IngestionStrategy]:
        """Lazy instantiation of strategies from registry."""
        # Normalize key to uppercase for consistency
        normalized_key = strategy_key.upper()
        if normalized_key not in self._strategy_instances:
            strategy_class = registry.get_strategy(normalized_key)
            if strategy_class:
                self._strategy_instances[normalized_key] = strategy_class()
        
        return self._strategy_instances.get(normalized_key)
