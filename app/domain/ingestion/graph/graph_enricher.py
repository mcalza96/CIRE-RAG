from typing import Any, Dict, List, Optional
from uuid import UUID
import structlog

from .graph_extractor import GraphExtractor
from app.infrastructure.supabase.repositories.supabase_graph_repository import SupabaseGraphRepository
from app.ai.generation import get_llm
from app.infrastructure.settings import settings
from app.infrastructure.observability.ingestion_logging import emit_event
from app.domain.ingestion.metadata.metadata_enricher import MetadataEnricher

logger = structlog.get_logger(__name__)

class GraphEnrichmentService:
    """
    Domain service for coordinating graph extraction and enrichment.
    """
    def __init__(self, graph_repository: Optional[SupabaseGraphRepository] = None):
        self.graph_repository = graph_repository or SupabaseGraphRepository()
        self.extractor = GraphExtractor(get_llm(temperature=0.0, capability="FORENSIC"))
        self.metadata_extractor = MetadataEnricher()

    async def run_enrichment(
        self,
        doc_id: str,
        tenant_id: str,
        chunks: List[Any],
        source_metadata: Optional[Dict[str, Any]] = None,
        log_step_callback: Optional[Any] = None
    ) -> Dict[str, int]:
        """
        Runs the full graph enrichment pipeline for a document's chunks.
        """
        tenant_uuid = UUID(str(tenant_id))
        embedding_mode = self._resolve_embedding_mode(chunks)
        embedding_provider = self._resolve_embedding_provider(chunks)
        
        graph_batch_size = max(1, int(getattr(settings, "INGESTION_GRAPH_BATCH_SIZE", 4) or 4))
        graph_log_every_n = max(1, int(getattr(settings, "INGESTION_GRAPH_CHUNK_LOG_EVERY_N", 25) or 25))

        totals = self._initialize_totals()

        # 1. Document Structure (TOC)
        toc_entries = self.metadata_extractor.extract_toc_entries(source_metadata or {})
        if toc_entries:
            try:
                structure_stats = await self.graph_repository.upsert_document_structure(
                    tenant_id=tenant_uuid,
                    source_document_id=UUID(str(doc_id)),
                    toc_entries=toc_entries,
                )
                totals["structure_nodes_upserted"] += int(structure_stats.get("nodes_upserted", 0))
                totals["structure_edges_upserted"] += int(structure_stats.get("edges_upserted", 0))
            except Exception as exc:
                logger.warning("toc_structure_upsert_failed", doc_id=doc_id, error=str(exc))

        # 2. Semantic Graph Extraction
        candidate_chunks = self._prepare_candidate_chunks(chunks, totals)
        
        for batch_start in range(0, len(candidate_chunks), graph_batch_size):
            batch = candidate_chunks[batch_start : batch_start + graph_batch_size]
            batch_texts = [item[2] for item in batch]
            batch_extractions = await self.extractor.extract_graph_batch_async(batch_texts)

            for (idx, chunk_uuid, _content), extraction in zip(batch, batch_extractions):
                if extraction.is_empty():
                    continue

                totals["chunks_with_graph"] += 1
                stats = await self.graph_repository.upsert_knowledge_subgraph(
                    extraction=extraction,
                    chunk_id=chunk_uuid,
                    tenant_id=tenant_uuid,
                    embedding_mode=embedding_mode,
                    embedding_provider=embedding_provider,
                )
                
                self._update_totals(totals, stats)
                
                if log_step_callback:
                    await self._log_chunk_progress(log_step_callback, doc_id, tenant_id, idx, chunk_uuid, stats, graph_log_every_n)

        # 3. Link Chunks to Structure
        if toc_entries:
            try:
                structure_link_stats = await self.graph_repository.link_chunks_to_structure(
                    tenant_id=tenant_uuid,
                    source_document_id=UUID(str(doc_id)),
                    chunks=chunks,
                )
                totals["structure_links_upserted"] += int(structure_link_stats.get("links_upserted", 0))
            except Exception as exc:
                logger.warning("chunk_structure_link_failed", doc_id=doc_id, error=str(exc))

        return totals

    def _initialize_totals(self) -> Dict[str, int]:
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
            "structure_nodes_upserted": 0,
            "structure_edges_upserted": 0,
            "structure_links_upserted": 0,
        }

    def _prepare_candidate_chunks(self, chunks: List[Any], totals: Dict[str, int]) -> List[tuple[int, UUID, str]]:
        candidate_chunks = []
        for idx, chunk in enumerate(chunks, start=1):
            content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
            chunk_id_raw = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
            metadata = chunk.get("metadata") if isinstance(chunk, dict) and isinstance(chunk.get("metadata"), dict) else {}
            
            totals["chunks_seen"] += 1
            if not content or not chunk_id_raw:
                continue
            if not bool(metadata.get("retrieval_eligible", True)):
                continue
            
            candidate_chunks.append((idx, UUID(str(chunk_id_raw)), str(content)))
        return candidate_chunks

    def _update_totals(self, totals: Dict[str, int], stats: Dict[str, Any]) -> None:
        totals["entities_extracted"] += stats.get("entities_extracted", 0)
        totals["relations_extracted"] += stats.get("relations_extracted", 0)
        totals["entities_inserted"] += stats.get("entities_inserted", 0)
        totals["entities_merged"] += stats.get("entities_merged", 0)
        totals["relations_inserted"] += stats.get("relations_inserted", 0)
        totals["relations_merged"] += stats.get("relations_merged", 0)
        totals["nodes_upserted"] += stats.get("nodes_upserted", 0)
        totals["edges_upserted"] += stats.get("edges_upserted", 0)
        totals["links_upserted"] += stats.get("links_upserted", 0)
        totals["chunk_errors"] += len(stats.get("errors", []))

    async def _log_chunk_progress(self, callback, doc_id, tenant_id, idx, chunk_uuid, stats, log_every):
        chunk_error_count = len(stats.get("errors", []))
        should_emit = chunk_error_count > 0 or idx == 1 or (idx % log_every) == 0
        
        if should_emit:
             emit_event(
                logger, "graphrag_chunk_metrics",
                doc_id=doc_id, chunk_index=idx, chunk_id=str(chunk_uuid),
                entities_extracted=stats.get("entities_extracted", 0),
                relations_extracted=stats.get("relations_extracted", 0),
                entities_inserted=stats.get("entities_inserted", 0),
                entities_merged=stats.get("entities_merged", 0),
                relations_inserted=stats.get("relations_inserted", 0),
                relations_merged=stats.get("relations_merged", 0),
                links_upserted=stats.get("links_upserted", 0),
                errors=chunk_error_count,
            )

        await callback(
            doc_id,
            f"Graph chunk {idx}: entities={stats.get('entities_extracted', 0)} provenance={stats.get('links_upserted', 0)}",
            "INFO" if chunk_error_count == 0 else "WARNING",
            tenant_id=tenant_id
        )

    @staticmethod
    def _resolve_embedding_mode(chunks: list[Any]) -> Optional[str]:
        for chunk in chunks:
            if not isinstance(chunk, dict): continue
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict): continue
            raw_mode = metadata.get("embedding_mode") or metadata.get("jina_mode")
            if not raw_mode: continue
            mode = str(raw_mode).upper().strip()
            if mode in {"LOCAL", "CLOUD"}: return mode
        return None

    @staticmethod
    def _resolve_embedding_provider(chunks: list[Any]) -> Optional[str]:
        for chunk in chunks:
            if not isinstance(chunk, dict): continue
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict): continue
            raw_provider = metadata.get("embedding_provider")
            if raw_provider:
                provider = str(raw_provider).strip().lower()
                if provider: return provider
            profile = metadata.get("embedding_profile")
            if isinstance(profile, dict) and profile.get("provider"):
                provider = str(profile.get("provider") or "").strip().lower()
                if provider: return provider
        return None
