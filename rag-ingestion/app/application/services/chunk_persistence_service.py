from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from app.application.services.ingestion_state_manager import IngestionStateManager
from app.domain.repositories.content_repository import IContentRepository


class ChunkPersistenceService:
    def __init__(self, content_repo: IContentRepository, state_manager: IngestionStateManager) -> None:
        self.content_repo = content_repo
        self.state_manager = state_manager

    async def persist_chunks(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        chunks: list[Any],
        collection_id: Optional[str] = None,
    ) -> int:
        self._attach_collection_scope(chunks, collection_id)
        self._ensure_chunk_ids(chunks)
        await self.state_manager.log_step(doc_id, f"Persistiendo {len(chunks)} fragmentos...", tenant_id=tenant_id)
        persisted_count = int(await self.content_repo.save_chunks(chunks) or 0)
        if persisted_count <= 0:
            raise RuntimeError(
                f"Audit Persistence Failure: strategies produced chunks but repository persisted 0 for doc_id={doc_id}"
            )
        return persisted_count

    @staticmethod
    async def cleanup_source(source: Any) -> None:
        if source:
            await source.close()

    @staticmethod
    def _ensure_chunk_ids(chunks: list[Any]) -> None:
        for chunk in chunks:
            cid = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
            if not cid:
                new_id = str(uuid4())
                if isinstance(chunk, dict):
                    chunk["id"] = new_id
                else:
                    setattr(chunk, "id", new_id)

    @staticmethod
    def _attach_collection_scope(chunks: list[Any], collection_id: Optional[str]) -> None:
        if not collection_id:
            return
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk["collection_id"] = collection_id
            metadata = chunk.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("collection_id", collection_id)
