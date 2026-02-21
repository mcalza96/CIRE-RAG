from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional
from uuid import UUID, uuid4
import json

import structlog

from app.services.ingestion.state.state_manager import IngestionStateManager
from app.infrastructure.settings import settings
from app.domain.repositories.content_repository import IContentRepository
from app.ai.llm import get_llm
from app.infrastructure.supabase.client import get_async_supabase_client
from app.infrastructure.supabase.repositories.supabase_graph_repository import SupabaseGraphRepository
from app.infrastructure.supabase.repositories.supabase_raptor_repository import SupabaseRaptorRepository
from app.services.knowledge.graph_extractor import GraphExtractor
from app.services.ingestion.anchors.anchor_service import VisualAnchorService
from app.infrastructure.observability.ingestion_logging import compact_error, emit_event

logger = structlog.get_logger(__name__)


class PostIngestionPipelineService:
    def __init__(
        self,
        content_repo: IContentRepository,
        state_manager: IngestionStateManager,
        raptor_processor: Any | None,
        raptor_repo: SupabaseRaptorRepository,
        visual_anchor_service: VisualAnchorService | None = None,
    ) -> None:
        self.content_repo = content_repo
        self.state_manager = state_manager
        self.raptor_processor = raptor_processor
        self.raptor_repo = raptor_repo
        self.visual_anchor_service = visual_anchor_service

    async def persist_chunks(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        chunks: list[Any],
        collection_id: Optional[str] = None,
    ) -> int:
        self._attach_collection_scope(chunks, collection_id)
        self._ensure_chunk_ids(chunks)
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
        client = await get_async_supabase_client()
        payload = {
            "source_document_id": str(doc_id),
            "collection_id": str(collection_id) if collection_id else None,
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }

        existing = await (
            client.table("job_queue")
            .select("id")
            .eq("job_type", "enrich_document")
            .in_("status", ["pending", "processing"])
            .contains("payload", {"source_document_id": str(doc_id)})
            .limit(1)
            .execute()
        )
        if existing.data:
            return False

        await (
            client.table("job_queue")
            .insert(
                {
                    "job_type": "enrich_document",
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "payload": payload,
                }
            )
            .execute()
        )
        return True

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
            return {
                "ok": False,
                "reason": "no_chunks",
                "source_document_id": str(doc_id),
            }

        source_metadata = await self._load_source_document_metadata(doc_id=doc_id)
        visual_tasks = self._extract_visual_tasks(source_metadata)
        routing = {"visual_tasks": visual_tasks} if visual_tasks else {}
        toc_entries = self._extract_toc_entries(source_metadata)
        result_obj = SimpleNamespace(
            chunks=chunks,
            metadata={
                "routing": routing,
                "toc_entries": toc_entries,
            },
        )

        visual_stats: Dict[str, Any] | None = None
        if include_visual and self.visual_anchor_service is not None and visual_tasks:
            try:
                visual_stats = await self.visual_anchor_service.run_if_needed(
                    doc_id=doc_id,
                    tenant_id=tenant_id,
                    result=result_obj,
                )
            except Exception as exc:
                logger.warning(
                    "deferred_visual_enrichment_failed",
                    doc_id=doc_id,
                    error=str(exc),
                )

        if include_raptor:
            await self.run_raptor_if_needed(
                doc_id=doc_id,
                tenant_id=tenant_id,
                result=result_obj,
                collection_id=collection_id,
            )
        if include_graph:
            await self.run_graph_if_needed(
                doc_id=doc_id,
                tenant_id=tenant_id,
                result=result_obj,
                source_metadata=source_metadata,
            )
        result_payload = {
            "ok": True,
            "source_document_id": str(doc_id),
            "chunks": len(chunks),
            "visual_attempted": int(visual_stats.get("attempted", 0))
            if isinstance(visual_stats, dict)
            else 0,
            "visual_stitched": int(visual_stats.get("stitched", 0))
            if isinstance(visual_stats, dict)
            else 0,
            "include_visual": bool(include_visual),
            "include_graph": bool(include_graph),
            "include_raptor": bool(include_raptor),
        }
        emit_event(
            logger,
            "deferred_enrichment_completed",
            doc_id=doc_id,
            tenant_id=tenant_id,
            chunks=len(chunks),
            include_visual=bool(include_visual),
            include_graph=bool(include_graph),
            include_raptor=bool(include_raptor),
            visual_stitched=result_payload.get("visual_stitched"),
        )
        return result_payload

    async def _load_persisted_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        client = await get_async_supabase_client()
        query = (
            client.table("content_chunks")
            .select("id,content,embedding,metadata,file_page_number")
            .eq("source_id", str(doc_id))
        )
        try:
            response = await query.order("created_at", desc=False).execute()
        except Exception:
            response = await query.execute()
        rows = response.data if isinstance(response.data, list) else []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["embedding"] = self._normalize_embedding(item.get("embedding"))
            normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_embedding(raw_embedding: Any) -> list[float]:
        if isinstance(raw_embedding, list):
            out: list[float] = []
            for value in raw_embedding:
                try:
                    out.append(float(value))
                except (TypeError, ValueError):
                    continue
            return out

        if isinstance(raw_embedding, str):
            text = raw_embedding.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                out: list[float] = []
                for value in parsed:
                    try:
                        out.append(float(value))
                    except (TypeError, ValueError):
                        continue
                return out
            if text.startswith("[") and text.endswith("]"):
                inner = text[1:-1]
                out: list[float] = []
                for token in inner.split(","):
                    token = token.strip()
                    if not token:
                        continue
                    try:
                        out.append(float(token))
                    except (TypeError, ValueError):
                        continue
                return out

        return []

    async def _load_source_document_metadata(self, doc_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()
        response = (
            await client.table("source_documents")
            .select("metadata")
            .eq("id", str(doc_id))
            .limit(1)
            .execute()
        )
        rows = response.data if isinstance(response.data, list) else []
        if not rows:
            return {}
        metadata = rows[0].get("metadata") if isinstance(rows[0], dict) else None
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _extract_visual_tasks(source_metadata: Dict[str, Any]) -> list[dict[str, Any]]:
        routing = (
            source_metadata.get("routing")
            if isinstance(source_metadata.get("routing"), dict)
            else {}
        )
        tasks_raw = routing.get("visual_tasks") if isinstance(routing, dict) else []
        tasks = tasks_raw if isinstance(tasks_raw, list) else []
        return [task for task in tasks if isinstance(task, dict)]

    async def run_raptor_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        collection_id: Optional[str] = None,
    ) -> None:
        if not self.raptor_processor or len(result.chunks) <= 5:
            return

        await self.state_manager.log_step(
            doc_id, "Iniciando procesamiento RAPTOR...", tenant_id=tenant_id
        )
        try:
            from app.domain.schemas.raptor_schemas import BaseChunk

            base_chunks = []
            for chunk in result.chunks:
                content = chunk.get("content") if isinstance(chunk, dict) else chunk.content
                embedding = chunk.get("embedding") if isinstance(chunk, dict) else chunk.embedding
                metadata = chunk.get("metadata") if isinstance(chunk, dict) else {}
                if not isinstance(metadata, dict):
                    metadata = {}
                chunk_id = uuid4()

                if content and embedding:
                    section_ref = str(metadata.get("section_ref") or "").strip() or None
                    section_node_id = None
                    section_node_id_raw = metadata.get("section_node_id")
                    if section_node_id_raw:
                        try:
                            section_node_id = UUID(str(section_node_id_raw))
                        except Exception:
                            section_node_id = None
                    base_chunks.append(
                        BaseChunk(
                            id=chunk_id,
                            content=content,
                            embedding=embedding,
                            tenant_id=UUID(tenant_id)
                            if tenant_id
                            else UUID("00000000-0000-0000-0000-000000000000"),
                            source_standard=str(metadata.get("source_standard") or "").strip()
                            or None,
                            section_ref=section_ref,
                            section_node_id=section_node_id,
                        )
                    )

            if not base_chunks:
                return

            embedding_mode = self._resolve_embedding_mode(result.chunks)
            tree_result = await self.raptor_processor.build_tree(
                base_chunks=base_chunks,
                tenant_id=UUID(tenant_id)
                if tenant_id
                else UUID("00000000-0000-0000-0000-000000000000"),
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
            emit_event(
                logger,
                "raptor_processing_failed",
                level="warning",
                doc_id=doc_id,
                tenant_id=tenant_id,
                error_code="RAPTOR_PROCESSING_FAILED",
                error=compact_error(exc),
            )
            await self.state_manager.log_step(
                doc_id, f"Error en RAPTOR (no fatal): {str(exc)}", "WARNING", tenant_id=tenant_id
            )

    async def run_graph_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            await self.state_manager.log_step(
                doc_id,
                "Iniciando extracción de grafo y trazabilidad chunk->nodo...",
                tenant_id=tenant_id,
            )
            graph_stats = await self._run_graph_enrichment(
                doc_id=doc_id,
                tenant_id=tenant_id,
                chunks=result.chunks,
                source_metadata=source_metadata,
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

            emit_event(
                logger,
                "graph_enrichment_failed",
                level="warning",
                doc_id=doc_id,
                tenant_id=tenant_id,
                error_code="GRAPH_ENRICHMENT_FAILED",
                error=compact_error(exc),
            )
            await self.state_manager.log_step(
                doc_id, f"Error en GraphRAG (no fatal): {err_text}", "WARNING", tenant_id=tenant_id
            )

    async def _run_graph_enrichment(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        chunks: list[Any],
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
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
        embedding_provider = self._resolve_embedding_provider(chunks)
        graph_batch_size = max(
            1,
            int(getattr(settings, "INGESTION_GRAPH_BATCH_SIZE", 4) or 4),
        )
        graph_log_every_n = max(
            1,
            int(getattr(settings, "INGESTION_GRAPH_CHUNK_LOG_EVERY_N", 25) or 25),
        )

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
            "structure_nodes_upserted": 0,
            "structure_edges_upserted": 0,
            "structure_links_upserted": 0,
        }

        toc_entries = self._extract_toc_entries(source_metadata or {})
        if toc_entries:
            try:
                structure_stats = await graph_repository.upsert_document_structure(
                    tenant_id=tenant_uuid,
                    source_document_id=UUID(str(doc_id)),
                    toc_entries=toc_entries,
                )
                totals["structure_nodes_upserted"] += int(structure_stats.get("nodes_upserted", 0))
                totals["structure_edges_upserted"] += int(structure_stats.get("edges_upserted", 0))
            except Exception as exc:
                logger.warning("toc_structure_upsert_failed", doc_id=doc_id, error=str(exc))

        candidate_chunks: list[tuple[int, UUID, str]] = []
        skipped_structural = 0
        for idx, chunk in enumerate(chunks, start=1):
            content = (
                chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
            )
            chunk_id_raw = (
                chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
            )
            metadata = (
                chunk.get("metadata")
                if isinstance(chunk, dict) and isinstance(chunk.get("metadata"), dict)
                else {}
            )
            totals["chunks_seen"] += 1
            if not content or not chunk_id_raw:
                continue
            if not bool(metadata.get("retrieval_eligible", True)):
                skipped_structural += 1
                continue
            candidate_chunks.append((idx, UUID(str(chunk_id_raw)), str(content)))

        if skipped_structural > 0:
            emit_event(
                logger,
                "graphrag_structural_chunks_skipped",
                doc_id=doc_id,
                tenant_id=tenant_id,
                skipped_chunks=skipped_structural,
            )

        for batch_start in range(0, len(candidate_chunks), graph_batch_size):
            batch = candidate_chunks[batch_start : batch_start + graph_batch_size]
            batch_texts = [item[2] for item in batch]
            batch_extractions = await extractor.extract_graph_batch_async(batch_texts)

            for (idx, chunk_uuid, _content), extraction in zip(batch, batch_extractions):
                if extraction.is_empty():
                    continue

                totals["chunks_with_graph"] += 1

                stats = await graph_repository.upsert_knowledge_subgraph(
                    extraction=extraction,
                    chunk_id=chunk_uuid,
                    tenant_id=tenant_uuid,
                    embedding_mode=embedding_mode,
                    embedding_provider=embedding_provider,
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

                should_log_chunk_detail = (
                    chunk_error_count > 0 or idx == 1 or (idx % graph_log_every_n) == 0
                )
                if should_log_chunk_detail:
                    emit_event(
                        logger,
                        "graphrag_chunk_metrics",
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
                        sampled=(chunk_error_count == 0 and idx != 1),
                        sample_every_n=graph_log_every_n,
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

        if toc_entries:
            try:
                structure_link_stats = await graph_repository.link_chunks_to_structure(
                    tenant_id=tenant_uuid,
                    source_document_id=UUID(str(doc_id)),
                    chunks=chunks,
                )
                totals["structure_links_upserted"] += int(
                    structure_link_stats.get("links_upserted", 0)
                )
            except Exception as exc:
                logger.warning("chunk_structure_link_failed", doc_id=doc_id, error=str(exc))

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

    @staticmethod
    def _resolve_embedding_provider(chunks: list[Any]) -> Optional[str]:
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict):
                continue
            raw_provider = metadata.get("embedding_provider")
            if raw_provider:
                provider = str(raw_provider).strip().lower()
                if provider:
                    return provider
            profile = metadata.get("embedding_profile")
            if isinstance(profile, dict) and profile.get("provider"):
                provider = str(profile.get("provider") or "").strip().lower()
                if provider:
                    return provider
        return None

    @staticmethod
    def _extract_toc_entries(source_metadata: Dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(source_metadata, dict):
            return []
        toc_raw = source_metadata.get("toc_structure")
        if not isinstance(toc_raw, dict):
            return []
        entries_raw = toc_raw.get("entries")
        if not isinstance(entries_raw, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in entries_raw:
            if isinstance(item, dict):
                entries.append(item)
        return entries
