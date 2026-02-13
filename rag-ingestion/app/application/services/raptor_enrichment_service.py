from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

import structlog

from app.application.services.ingestion_state_manager import IngestionStateManager
from app.infrastructure.repositories.supabase_raptor_repository import SupabaseRaptorRepository

logger = structlog.get_logger(__name__)


class RaptorEnrichmentService:
    def __init__(
        self,
        state_manager: IngestionStateManager,
        raptor_processor: Any | None,
        raptor_repo: SupabaseRaptorRepository,
    ) -> None:
        self.state_manager = state_manager
        self.raptor_processor = raptor_processor
        self.raptor_repo = raptor_repo

    async def run_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        collection_id: Optional[str] = None,
    ) -> None:
        if not self.raptor_processor or len(result.chunks) <= 5:
            return

        await self.state_manager.log_step(doc_id, "Iniciando procesamiento RAPTOR...", tenant_id=tenant_id)
        try:
            from app.domain.raptor_schemas import BaseChunk

            base_chunks = []
            for chunk in result.chunks:
                content = chunk.get("content") if isinstance(chunk, dict) else chunk.content
                embedding = chunk.get("embedding") if isinstance(chunk, dict) else chunk.embedding
                chunk_id = uuid4()

                if content and embedding:
                    base_chunks.append(
                        BaseChunk(
                            id=chunk_id,
                            content=content,
                            embedding=embedding,
                            tenant_id=UUID(tenant_id) if tenant_id else UUID("00000000-0000-0000-0000-000000000000"),
                        )
                    )

            if not base_chunks:
                return

            embedding_mode = self._resolve_embedding_mode(result.chunks)
            tree_result = await self.raptor_processor.build_tree(
                base_chunks=base_chunks,
                tenant_id=UUID(tenant_id) if tenant_id else UUID("00000000-0000-0000-0000-000000000000"),
                source_document_id=UUID(doc_id),
                collection_id=UUID(collection_id) if collection_id else None,
                embedding_mode=embedding_mode,
            )

            if collection_id:
                await self.raptor_repo.backfill_collection_id(doc_id, collection_id)

            await self.state_manager.log_step(
                doc_id,
                f"RAPTOR completado: {tree_result.total_nodes_created} nodos resumen.",
                "SUCCESS",
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.error(f"RAPTOR processing failed for {doc_id}: {exc}")
            await self.state_manager.log_step(doc_id, f"Error en RAPTOR (no fatal): {str(exc)}", "WARNING", tenant_id=tenant_id)

    @staticmethod
    def _resolve_embedding_mode(chunks: list[Any]) -> Optional[str]:
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict):
                continue
            raw_mode = metadata.get("embedding_mode") or metadata.get("jina_mode")
            if not raw_mode:
                continue
            mode = str(raw_mode).upper().strip()
            if mode in {"LOCAL", "CLOUD"}:
                return mode
        return None
