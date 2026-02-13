from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID, uuid4

import structlog

from app.application.services.ingestion_state_manager import IngestionStateManager
from app.domain.repositories.content_repository import IContentRepository
from app.core.llm import get_llm
from app.infrastructure.repositories.supabase_graph_repository import SupabaseGraphRepository
from app.infrastructure.repositories.supabase_raptor_repository import SupabaseRaptorRepository
from app.services.knowledge.graph_extractor import GraphExtractor

logger = structlog.get_logger(__name__)


class PostIngestionPipelineService:
    def __init__(
        self,
        content_repo: IContentRepository,
        state_manager: IngestionStateManager,
        raptor_processor: Any | None,
        raptor_repo: SupabaseRaptorRepository,
    ) -> None:
        self.content_repo = content_repo
        self.state_manager = state_manager
        self.raptor_processor = raptor_processor
        self.raptor_repo = raptor_repo

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

    async def run_raptor_if_needed(
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

    async def run_graph_if_needed(self, doc_id: str, tenant_id: Optional[str], result: Any) -> None:
        try:
            await self.state_manager.log_step(doc_id, "Iniciando extracción de grafo y trazabilidad chunk->nodo...", tenant_id=tenant_id)
            graph_stats = await self._run_graph_enrichment(
                doc_id=doc_id,
                tenant_id=tenant_id,
                chunks=result.chunks,
            )
            await self.state_manager.log_step(
                doc_id,
                (
                    "Grafo completado: "
                    f"chunks={graph_stats['chunks_with_graph']}/{graph_stats['chunks_seen']}, "
                    f"entidades_extraidas={graph_stats['entities_extracted']} "
                    f"(new={graph_stats['entities_inserted']}, merged={graph_stats['entities_merged']}), "
                    f"relaciones_extraidas={graph_stats['relations_extracted']} "
                    f"(new={graph_stats['relations_inserted']}, merged={graph_stats['relations_merged']}), "
                    f"nodos={graph_stats['nodes_upserted']}, "
                    f"aristas={graph_stats['edges_upserted']}, "
                    f"vinculos={graph_stats['links_upserted']}, "
                    f"errores_chunk={graph_stats['chunk_errors']}"
                ),
                "SUCCESS",
                tenant_id=tenant_id,
                metadata={
                    "phase": "graph_enrichment_summary",
                    "metrics": graph_stats,
                },
            )
        except Exception as exc:
            err_text = str(exc)
            if "instructor" in err_text.lower() and "required" in err_text.lower():
                logger.warning(f"Graph extraction skipped for {doc_id}: {err_text}")
                await self.state_manager.log_step(
                    doc_id,
                    "GraphRAG omitido: falta dependencia opcional 'instructor' (no fatal).",
                    "WARNING",
                    tenant_id=tenant_id,
                )
                return

            logger.error(f"Graph extraction failed for {doc_id}: {exc}")
            await self.state_manager.log_step(doc_id, f"Error en GraphRAG (no fatal): {err_text}", "WARNING", tenant_id=tenant_id)

    async def _run_graph_enrichment(self, doc_id: str, tenant_id: Optional[str], chunks: list[Any]) -> Dict[str, int]:
        if not tenant_id:
            logger.warning("Skipping graph enrichment: missing tenant_id", doc_id=doc_id)
            return {
                "chunks_seen": 0,
                "chunks_with_graph": 0,
                "entities_extracted": 0,
                "relations_extracted": 0,
                "entities_inserted": 0,
                "entities_merged": 0,
                "relations_inserted": 0,
                "relations_merged": 0,
                "nodes_upserted": 0,
                "edges_upserted": 0,
                "links_upserted": 0,
                "chunk_errors": 0,
            }

        tenant_uuid = UUID(str(tenant_id))
        graph_repository = SupabaseGraphRepository()
        extractor = GraphExtractor(get_llm(temperature=0.0, capability="FORENSIC"))
        embedding_mode = self._resolve_embedding_mode(chunks)

        totals = {
            "chunks_seen": 0,
            "chunks_with_graph": 0,
            "entities_extracted": 0,
            "relations_extracted": 0,
            "entities_inserted": 0,
            "entities_merged": 0,
            "relations_inserted": 0,
            "relations_merged": 0,
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "links_upserted": 0,
            "chunk_errors": 0,
        }

        for idx, chunk in enumerate(chunks, start=1):
            content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
            chunk_id_raw = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
            totals["chunks_seen"] += 1

            if not content or not chunk_id_raw:
                continue

            chunk_uuid = UUID(str(chunk_id_raw))
            extraction = await extractor.extract_graph_from_chunk_async(text=content)
            if extraction.is_empty():
                continue

            totals["chunks_with_graph"] += 1

            stats = await graph_repository.upsert_knowledge_subgraph(
                extraction=extraction,
                chunk_id=chunk_uuid,
                tenant_id=tenant_uuid,
                embedding_mode=embedding_mode,
            )
            totals["entities_extracted"] += stats.get("entities_extracted", 0)
            totals["relations_extracted"] += stats.get("relations_extracted", 0)
            totals["entities_inserted"] += stats.get("entities_inserted", 0)
            totals["entities_merged"] += stats.get("entities_merged", 0)
            totals["relations_inserted"] += stats.get("relations_inserted", 0)
            totals["relations_merged"] += stats.get("relations_merged", 0)
            totals["nodes_upserted"] += stats.get("nodes_upserted", 0)
            totals["edges_upserted"] += stats.get("edges_upserted", 0)
            totals["links_upserted"] += stats.get("links_upserted", 0)

            chunk_error_count = len(stats.get("errors", []))
            totals["chunk_errors"] += chunk_error_count

            logger.info(
                "GraphRAG chunk metrics",
                doc_id=doc_id,
                chunk_index=idx,
                chunk_id=str(chunk_uuid),
                entities_extracted=stats.get("entities_extracted", 0),
                relations_extracted=stats.get("relations_extracted", 0),
                entities_inserted=stats.get("entities_inserted", 0),
                entities_merged=stats.get("entities_merged", 0),
                relations_inserted=stats.get("relations_inserted", 0),
                relations_merged=stats.get("relations_merged", 0),
                links_upserted=stats.get("links_upserted", 0),
                errors=chunk_error_count,
            )

            await self.state_manager.log_step(
                doc_id,
                (
                    f"Graph chunk {idx}: "
                    f"entities={stats.get('entities_extracted', 0)} "
                    f"(new={stats.get('entities_inserted', 0)}, merged={stats.get('entities_merged', 0)}), "
                    f"relations={stats.get('relations_extracted', 0)} "
                    f"(new={stats.get('relations_inserted', 0)}, merged={stats.get('relations_merged', 0)}), "
                    f"provenance={stats.get('links_upserted', 0)}, errors={chunk_error_count}"
                ),
                "INFO" if chunk_error_count == 0 else "WARNING",
                tenant_id=tenant_id,
                metadata={
                    "phase": "graph_enrichment_chunk",
                    "chunk_index": idx,
                    "chunk_id": str(chunk_uuid),
                    "metrics": {
                        "entities_extracted": stats.get("entities_extracted", 0),
                        "relations_extracted": stats.get("relations_extracted", 0),
                        "entities_inserted": stats.get("entities_inserted", 0),
                        "entities_merged": stats.get("entities_merged", 0),
                        "relations_inserted": stats.get("relations_inserted", 0),
                        "relations_merged": stats.get("relations_merged", 0),
                        "nodes_upserted": stats.get("nodes_upserted", 0),
                        "edges_upserted": stats.get("edges_upserted", 0),
                        "links_upserted": stats.get("links_upserted", 0),
                        "errors": chunk_error_count,
                    },
                },
            )

        return totals

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
