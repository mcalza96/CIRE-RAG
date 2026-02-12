from abc import ABC, abstractmethod
from enum import Enum

class RetryAction(Enum):
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"

class IIngestionPolicy(ABC):
    """
    Interface for ingestion pick-up and retry policies.
    """
    
    @abstractmethod
    def should_process(self, status: str, meta_status: str, metadata: dict = None) -> bool:
        """
        Determines if a document should be picked up for processing.
        """
        pass

    @abstractmethod
    def determine_retry_action(self, retry_count: int) -> RetryAction:
        """
        Determines the next action based on failure count.
        """
        pass

    @abstractmethod
    def validate_tenant_isolation(self, is_global: bool, institution_id: str) -> None:
        """
        Validates multitenancy constraints.
        """
        pass
