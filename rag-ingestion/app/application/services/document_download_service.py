import os
import structlog
from typing import Optional
from app.domain.interfaces.storage_service_interface import IStorageService
from app.domain.models.ingestion_source import IngestionSource
from app.domain.repositories.source_repository import ISourceRepository
from app.core.settings import settings

logger = structlog.get_logger(__name__)

class DocumentDownloadService:
    """
    Service responsible for retrieving document binary data.
    Abstracts local filesystem vs cloud storage (Supabase).
    """
    
    def __init__(self, storage_service: IStorageService, repository: ISourceRepository):
        self.storage = storage_service
        self.repo = repository

    async def download(
        self, 
        doc_id: str, 
        storage_path: str, 
        filename: str, 
        tenant_id: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> IngestionSource:
        """
        Retrieves the file. 
        Priority: 
        1. Local File System (Manual/CLI ingestion)
        2. Supabase Storage (Institutional ingestion)
        """
        # 1. Local Filesystem Check
        if os.path.exists(storage_path):
            await self.repo.log_event(
                doc_id, 
                f"Archivo detectado localmente: {storage_path}", 
                "INFO", 
                tenant_id=tenant_id
            )
            from app.infrastructure.adapters.filesystem_ingestion_source import FileSystemIngestionSource
            return FileSystemIngestionSource(storage_path, filename)
            
        # 2. Supabase Storage Download
        await self.repo.log_event(
            doc_id, 
            f"Descargando desde storage: {storage_path}", 
            "INFO", 
            tenant_id=tenant_id
        )
        
        buckets: list[str] = []
        for b in [
            bucket_name,
            settings.RAG_STORAGE_BUCKET,
            settings.INSTITUTIONAL_STORAGE_BUCKET,
        ]:
            if not b:
                continue
            if b not in buckets:
                buckets.append(b)

        last_error: Exception | None = None
        for bucket in buckets:
            try:
                return await self.storage.download_to_temp(storage_path, filename, bucket_name=bucket)
            except Exception as e:
                last_error = e
                logger.warning(
                    "download_attempt_failed",
                    doc_id=doc_id,
                    storage_path=storage_path,
                    bucket=bucket,
                    error=str(e),
                )

        logger.error("download_failed", doc_id=doc_id, storage_path=storage_path, buckets=buckets, error=str(last_error))
        raise ValueError(f"Could not retrieve file from {storage_path}: {last_error}")
