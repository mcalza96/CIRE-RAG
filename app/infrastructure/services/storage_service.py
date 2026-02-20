import os
import tempfile
import asyncio
import httpx
import httpcore
from typing import Optional
from app.infrastructure.supabase.client import get_async_supabase_client, reset_async_supabase_client
from app.infrastructure.adapters.filesystem_ingestion_source import FileSystemIngestionSource
import logging
from app.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# Errores transitorios que justifican un reintento
TRANSIENT_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectError,
    httpx.WriteError,  # Added: HTTP/2 write failures
    httpcore.ConnectTimeout,
    httpcore.ReadTimeout,
    httpcore.WriteError,
    ConnectionResetError,
)

class StorageService:
    MAX_RETRIES = 3
    BASE_DELAY_SECONDS = 1.0
    
    def __init__(self, bucket_name: Optional[str] = None):
        self.bucket_name = bucket_name or settings.RAG_STORAGE_BUCKET
        self._client = None

    async def get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client
    
    async def _reset_client(self):
        """Fuerza la recreación del cliente Supabase para limpiar conexiones corruptas."""
        logger.warning("[StorageService] Reseteando cliente Supabase global tras error de conexión...")
        reset_async_supabase_client()  # Invalida el singleton global
        self._client = None

    async def download_to_temp(self, storage_path: str, filename: str, bucket_name: Optional[str] = None) -> FileSystemIngestionSource:
        """
        Downloads a file with retry logic for transient network errors.
        Uses exponential backoff (1s, 2s, 4s) between retries.
        """
        extension = os.path.splitext(filename)[1]
        
        # Create temp file before retries
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            temp_path = tmp.name
        
        last_error: Exception | None = None
        
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                client = await self.get_client()
                target_bucket = bucket_name or self.bucket_name
                logger.info(f"[StorageService] Descargando {storage_path} de {target_bucket} (intento {attempt}/{self.MAX_RETRIES})")
                
                res = await client.storage.from_(target_bucket).download(storage_path)
                
                with open(temp_path, 'wb+') as f:
                    f.write(res)
                
                logger.info(f"[StorageService] Descarga exitosa: {filename}")
                return FileSystemIngestionSource(temp_path, filename)
                
            except TRANSIENT_ERRORS as e:
                last_error = e
                delay = self.BASE_DELAY_SECONDS * (2 ** (attempt - 1))  # Exponential backoff
                logger.warning(
                    f"[StorageService] Error transitorio en intento {attempt}: {type(e).__name__}. "
                    f"Reintentando en {delay:.1f}s..."
                )
                # Resetear cliente para forzar nueva conexión
                await self._reset_client()
                await asyncio.sleep(delay)
            except Exception as e:
                # Errores no transitorios se propagan inmediatamente
                logger.error(f"[StorageService] Error no transitorio: {type(e).__name__}: {e}")
                raise
        
        # Si llegamos aquí, todos los reintentos fallaron
        logger.error(f"[StorageService] Descarga fallida tras {self.MAX_RETRIES} intentos: {storage_path}")
        raise last_error or RuntimeError("Download failed after all retries")
