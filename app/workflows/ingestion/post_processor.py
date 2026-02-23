from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional, List
from uuid import UUID, uuid4

import structlog

from app.infrastructure.state_management.state_manager import IngestionStateManager
from app.domain.ingestion.ports import IContentRepository, ISourceRepository
from app.infrastructure.supabase.repositories.supabase_raptor_repository import (
    SupabaseRaptorRepository,
)
from app.infrastructure.supabase.repositories.supabase_graph_repository import (
    SupabaseGraphRepository,
)
from app.infrastructure.supabase.repositories.job_repository import JobRepository
from app.domain.ingestion.graph.graph_enricher import GraphEnrichmentService
from app.domain.ingestion.graph.graph_extractor import GraphExtractor
from app.domain.ingestion.visual.context_service import VisualContextService
from app.domain.ingestion.metadata.metadata_enricher import MetadataEnricher
from app.domain.ingestion.chunking.text_normalization import normalize_embedding, ensure_chunk_ids
from app.infrastructure.observability.ingestion_logging import compact_error, emit_event
from app.ai.generation import get_strict_engine
from app.infrastructure.settings import settings

logger = structlog.get_logger(__name__)


class PostIngestionPipelineService:
    """
    Orchestrator for post-ingestion tasks: persisting chunks, and running
    enrichments (GraphRAG, RAPTOR, Visual Anchors).
    """

    def __init__(
        self,
        content_repo: IContentRepository,
        state_manager: IngestionStateManager,
        source_repo: Optional[ISourceRepository] = None,
        raptor_processor: Any | None = None,
        raptor_repo: Optional[SupabaseRaptorRepository] = None,
        visual_context_service: Optional[VisualContextService] = None,
        graph_enrichment_service: Optional[GraphEnrichmentService] = None,
        job_repository: Optional[JobRepository] = None,
    ) -> None:
        self.content_repo = content_repo
        self.state_manager = state_manager
        self.source_repo = source_repo
        self.raptor_processor = raptor_processor
        self.raptor_repo = raptor_repo
        self.visual_context_service = visual_context_service
        self.graph_enrichment_service = graph_enrichment_service or GraphEnrichmentService(
            graph_repository=SupabaseGraphRepository(),
            extractor=GraphExtractor(
                strict_engine=get_strict_engine(),
                max_concurrency=max(
                    1,
                    int(getattr(settings, "GRAPH_EXTRACTION_MAX_CONCURRENCY", 6) or 6),
                ),
                retry_max_attempts=max(
                    1,
                    int(getattr(settings, "GRAPH_EXTRACTION_RETRY_MAX_ATTEMPTS", 3) or 3),
                ),
                retry_base_delay_seconds=float(
                    getattr(settings, "GRAPH_EXTRACTION_RETRY_BASE_DELAY_SECONDS", 0.8) or 0.8
                ),
                retry_max_delay_seconds=float(
                    getattr(settings, "GRAPH_EXTRACTION_RETRY_MAX_DELAY_SECONDS", 8.0) or 8.0
                ),
                retry_jitter_seconds=float(
                    getattr(settings, "GRAPH_EXTRACTION_RETRY_JITTER_SECONDS", 0.35) or 0.35
                ),
                error_formatter=compact_error,
            ),
            log_event_emitter=emit_event,
            graph_batch_size=max(1, int(getattr(settings, "INGESTION_GRAPH_BATCH_SIZE", 4) or 4)),
            graph_log_every_n=max(
                1,
                int(getattr(settings, "INGESTION_GRAPH_CHUNK_LOG_EVERY_N", 25) or 25),
            ),
        )
        self.job_repository = job_repository or JobRepository()
        self.metadata_extractor = MetadataEnricher()

    async def persist_chunks(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        chunks: list[Any],
        collection_id: Optional[str] = None,
    ) -> int:
        self._attach_collection_scope(chunks, collection_id)
        ensure_chunk_ids(chunks)
        await self.state_manager.log_step(
            doc_id, f"Persistiendo {len(chunks)} fragmentos...", tenant_id=tenant_id
        )
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

    async def enqueue_deferred_enrichment(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        collection_id: Optional[str] = None,
        *,
        include_visual: bool = False,
        include_graph: bool = True,
        include_raptor: bool = True,
    ) -> bool:
        payload = {
            "source_document_id": str(doc_id),
            "collection_id": str(collection_id) if collection_id else None,
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }
        return await self.job_repository.enqueue_enrichment_job(doc_id, tenant_id, payload)

    async def run_deferred_enrichment(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        collection_id: Optional[str] = None,
        *,
        include_visual: bool = False,
        include_graph: bool = True,
        include_raptor: bool = True,
    ) -> Dict[str, Any]:
        chunks = await self._load_persisted_chunks(doc_id=doc_id)
        if not chunks:
            return {"ok": False, "reason": "no_chunks", "source_document_id": str(doc_id)}

        source_metadata = await self._load_source_document_metadata(doc_id=doc_id)
        visual_tasks = self.metadata_extractor.extract_visual_tasks(source_metadata)
        toc_entries = self.metadata_extractor.extract_toc_entries(source_metadata)

        result_obj = SimpleNamespace(
            chunks=chunks,
            metadata={
                "routing": {"visual_tasks": visual_tasks} if visual_tasks else {},
                "toc_entries": toc_entries,
            },
        )

        visual_stats: Optional[Dict[str, Any]] = None
        if include_visual and self.visual_context_service and visual_tasks:
            try:
                visual_stats = await self.visual_context_service.process_visual_tasks(
                    doc_id=doc_id, tenant_id=tenant_id, result=result_obj
                )
            except Exception as exc:
                logger.warning("deferred_visual_enrichment_failed", doc_id=doc_id, error=str(exc))

        if include_raptor:
            await self.run_raptor_if_needed(doc_id, tenant_id, result_obj, collection_id)

        if include_graph:
            await self.run_graph_if_needed(doc_id, tenant_id, result_obj, source_metadata)

        return {
            "ok": True,
            "source_document_id": str(doc_id),
            "chunks": len(chunks),
            "visual_stitched": int(visual_stats.get("stitched", 0)) if visual_stats else 0,
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }

    async def run_raptor_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        collection_id: Optional[str] = None,
    ) -> None:
        if not self.raptor_processor or not self.raptor_repo or len(result.chunks) <= 5:
            return

        await self.state_manager.log_step(
            doc_id, "Iniciando procesamiento RAPTOR...", tenant_id=tenant_id
        )
        try:
            from app.domain.schemas.raptor_schemas import BaseChunk

            base_chunks = self._map_to_raptor_chunks(result.chunks, tenant_id)
            if not base_chunks:
                return

            tree_result = await self.raptor_processor.build_tree(
                base_chunks=base_chunks,
                tenant_id=UUID(tenant_id)
                if tenant_id
                else UUID("00000000-0000-0000-0000-000000000000"),
                source_document_id=UUID(doc_id),
                collection_id=UUID(collection_id) if collection_id else None,
                embedding_mode=self.graph_enrichment_service._resolve_embedding_mode(result.chunks),
            )

            if collection_id:
                await self.raptor_repo.backfill_collection_id(doc_id, collection_id)

            await self.state_manager.log_step(
                doc_id,
                f"RAPTOR completado: {tree_result.total_nodes_created} nodos.",
                "SUCCESS",
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.warning("raptor_processing_failed", doc_id=doc_id, error=str(exc))

    async def run_graph_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not tenant_id:
            return
        try:
            await self.state_manager.log_step(
                doc_id, "Iniciando extracción de grafo...", tenant_id=tenant_id
            )
            stats = await self.graph_enrichment_service.run_enrichment(
                doc_id=doc_id,
                tenant_id=tenant_id,
                chunks=result.chunks,
                source_metadata=source_metadata,
                log_step_callback=self.state_manager.log_step,
            )
            await self.state_manager.log_step(
                doc_id,
                f"Grafo completado: nodes={stats['nodes_upserted']}, edges={stats['edges_upserted']}",
                "SUCCESS",
                tenant_id=tenant_id,
                metadata={"metrics": stats},
            )
        except Exception as exc:
            logger.warning("graph_enrichment_failed", doc_id=doc_id, error=str(exc))

    async def _load_persisted_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        rows = await self.content_repo.get_chunks_by_source_id(doc_id)
        for row in rows:
            if isinstance(row, dict):
                row["embedding"] = normalize_embedding(row.get("embedding"))
        return rows

    async def _load_source_document_metadata(self, doc_id: str) -> Dict[str, Any]:
        if not self.source_repo:
            return {}
        doc = await self.source_repo.get_by_id(doc_id)
        if not doc:
            return {}
        return doc.get("metadata") or {}

    def _map_to_raptor_chunks(self, chunks: List[Any], tenant_id: Optional[str]) -> List[Any]:
        from app.domain.schemas.raptor_schemas import BaseChunk

        base_chunks = []
        for chunk in chunks:
            content = (
                chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
            )
            embedding = (
                chunk.get("embedding")
                if isinstance(chunk, dict)
                else getattr(chunk, "embedding", None)
            )
            metadata = (
                chunk.get("metadata", {})
                if isinstance(chunk, dict)
                else getattr(chunk, "metadata", {})
            )

            if content and embedding:
                base_chunks.append(
                    BaseChunk(
                        id=uuid4(),
                        content=content,
                        embedding=embedding,
                        tenant_id=UUID(tenant_id)
                        if tenant_id
                        else UUID("00000000-0000-0000-0000-000000000000"),
                        source_standard=str(metadata.get("source_standard") or "").strip() or None,
                    )
                )
        return base_chunks

    @staticmethod
    def _attach_collection_scope(chunks: list[Any], collection_id: Optional[str]) -> None:
        if not collection_id:
            return
        for chunk in chunks:
            if isinstance(chunk, dict):
                chunk["collection_id"] = collection_id
                metadata = chunk.setdefault("metadata", {})
                metadata["collection_id"] = collection_id
