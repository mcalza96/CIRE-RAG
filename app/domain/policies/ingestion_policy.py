from app.domain.types.ingestion_status import IngestionStatus
from enum import Enum
from typing import Optional, Dict, Any


class RetryAction(Enum):
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


class IngestionPolicy:
    """
    Domain Service that encapsulates the rules for when a document 
    should be picked up for processing by the ingestion pipeline.
    """
    
    # Statuses that indicate a document is waiting for ingestion
    PENDING_STATES = {
        IngestionStatus.PENDING.value,
        IngestionStatus.PENDING_INGESTION.value,
        IngestionStatus.QUEUED.value
    }

    MAX_RETRIES = 3

    def should_process(self, status: str, meta_status: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Determines if a document should be processed based on its current status, 
        metadata status, and retry threshold.
        """
        is_pending = (status in self.PENDING_STATES) or (meta_status in self.PENDING_STATES)
        
        if not is_pending:
            return False
            
        # Check retry count to prevent infinite loops
        retry_count = (metadata or {}).get("retry_count", 0)
        if retry_count >= self.MAX_RETRIES:
            return False
            
        return True

    def determine_retry_action(self, retry_count: int) -> RetryAction:
        """
        Determines the next action based on the current retry count.
        """
        if retry_count >= self.MAX_RETRIES:
            return RetryAction.DEAD_LETTER
        return RetryAction.RETRY

    def validate_tenant_isolation(self, is_global: bool, institution_id: str) -> None:
        """
        Enforce Multitenancy Isolation Rule:
        - If is_global is False, institution_id MUST be present.
        """
        if not is_global:
            if not institution_id or institution_id == "00000000-0000-0000-0000-000000000000":
                raise ValueError("Tenant Isolation Violation: Non-global document missing institution_id.")
