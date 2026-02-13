import structlog
from typing import Dict, Any, Optional
from app.domain.repositories.source_repository import ISourceRepository
from app.domain.types.ingestion_status import IngestionStatus

logger = structlog.get_logger(__name__)

class IngestionStateManager:
    """
    Expert in managing document state transitions and event logging.
    Centralizes repository interactions for ingestion lifecycle.
    """
    
    def __init__(self, repository: ISourceRepository):
        self.repo = repository

    async def start_processing(self, doc_id: str, filename: str, metadata: Dict[str, Any], tenant_id: Optional[str] = None):
        await self.repo.log_event(doc_id, f"Iniciando procesamiento: {filename}", "INFO", tenant_id=tenant_id)
        metadata["status"] = IngestionStatus.PROCESSING.value
        await self.repo.update_status_and_metadata(doc_id, IngestionStatus.PROCESSING.value, metadata)

    async def handle_success(self, doc_id: str, chunks_count: int, metadata: Dict[str, Any], tenant_id: Optional[str] = None):
        metadata["status"] = IngestionStatus.SUCCESS.value
        metadata["chunks_count"] = chunks_count
        
        await self.repo.update_status_and_metadata(
            doc_id, 
            IngestionStatus.SUCCESS.value, 
            metadata
        )
        await self.repo.log_event(doc_id, "Procesamiento exitoso.", "SUCCESS", tenant_id=tenant_id)

    async def handle_error(self, doc_id: str, error: Exception, metadata: Dict[str, Any], tenant_id: Optional[str] = None):
        error_msg = str(error)
        logger.error("ingestion_error", doc_id=doc_id, error=error_msg)
        
        metadata["status"] = IngestionStatus.FAILED.value
        metadata["error"] = error_msg
        
        await self.repo.update_status_and_metadata(
            doc_id, 
            IngestionStatus.FAILED.value, 
            metadata
        )
        await self.repo.log_event(doc_id, f"Error critico: {error_msg}", "ERROR", tenant_id=tenant_id)
        
    async def log_step(
        self,
        doc_id: str,
        message: str,
        status: str = "INFO",
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        await self.repo.log_event(doc_id, message, status, tenant_id=tenant_id, metadata=metadata)
