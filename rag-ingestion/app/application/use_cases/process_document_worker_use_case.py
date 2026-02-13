import os
import logging
from typing import Dict, Any, Optional
from uuid import UUID, uuid4

from app.domain.repositories.source_repository import ISourceRepository
from app.domain.repositories.content_repository import IContentRepository
from app.domain.interfaces.ingestion_dispatcher_interface import IIngestionDispatcher
from app.schemas.ingestion import IngestionMetadata
from app.domain.types.ingestion_status import IngestionStatus
from app.domain.interfaces.ingestion_policy_interface import IIngestionPolicy, RetryAction
from app.domain.interfaces.taxonomy_manager_interface import ITaxonomyManager
from app.domain.models.ingestion_source import IngestionSource
from app.infrastructure.adapters.supabase_metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.services.storage_service import StorageService
from app.core.observability.correlation import set_correlation_id
from app.application.services.ingestion_context_resolver import IngestionContextResolver
import structlog
from app.core.observability.logger_config import bind_context
from app.core.llm import get_llm
from app.services.knowledge.graph_extractor import GraphExtractor
from app.infrastructure.supabase.client import get_async_supabase_client
from app.infrastructure.repositories.supabase_graph_repository import SupabaseGraphRepository
from app.application.services.document_download_service import DocumentDownloadService
from app.application.services.ingestion_state_manager import IngestionStateManager
from app.application.services.visual_anchor_service import VisualAnchorService
from app.services.ingestion.visual_parser import VisualDocumentParser
from app.services.ingestion.integrator import VisualGraphIntegrator

logger = structlog.get_logger(__name__)

class ProcessDocumentWorkerUseCase:
    """
    Orchestrates the document ingestion workflow.
    Refactored to enforce SRP, Atomic State correctness, and Retry awareness.
    Adheres to DIP by injecting all infrastructure dependencies.
    """
    def __init__(
        self, 
        repository: ISourceRepository, 
        content_repo: IContentRepository,
        storage_service: StorageService,
        dispatcher: IIngestionDispatcher,
        taxonomy_manager: ITaxonomyManager,
        metadata_adapter: SupabaseMetadataAdapter,
        policy: IIngestionPolicy,
        resolver: Optional[IngestionContextResolver] = None,
        # NEW: Inject RAPTOR Processor
        raptor_processor: Optional[Any] = None,
        visual_parser: Optional[VisualDocumentParser] = None,
        visual_integrator: Optional[VisualGraphIntegrator] = None,
        # INJECTED SERVICES
        download_service: Optional[DocumentDownloadService] = None,
        state_manager: Optional[IngestionStateManager] = None
    ):
        self.repo = repository
        self.content_repo = content_repo
        self.storage = storage_service
        self.dispatcher = dispatcher
        self.taxonomy_manager = taxonomy_manager
        self.metadata_adapter = metadata_adapter
        self.policy = policy
        self.resolver = resolver or IngestionContextResolver()
        self.raptor_processor = raptor_processor
        self.visual_parser = visual_parser or VisualDocumentParser()
        self.visual_integrator = visual_integrator or VisualGraphIntegrator()
        
        # Initialize or use injected services
        self.download_service = download_service or DocumentDownloadService(storage_service, repository)
        self.state_manager = state_manager or IngestionStateManager(repository)
        self.visual_anchor_service = VisualAnchorService(
            state_manager=self.state_manager,
            visual_parser=self.visual_parser,
            visual_integrator=self.visual_integrator,
        )

    async def execute(self, record: Dict[str, Any]):
        # 0. Context Recovery & Observability
        doc_id, correlation_id, course_id = self.resolver.extract_observability_context(record)
        if not doc_id:
            logger.error(f"[UseCase] Record missing ID: {record}")
            return

        set_correlation_id(correlation_id)
        bind_context(course_id=course_id)
        
        logger.info("Ingestión de documento iniciada", doc_id=doc_id, correlation_id=correlation_id, course_id=course_id)

        # 1. Domain Policy Guard
        current_status = record.get('status')
        meta = record.get('metadata', {}) or {}
        if not self.policy.should_process(current_status, meta.get('status'), metadata=meta):
            logger.debug(f"[UseCase] Document {doc_id} ignored by policy.")
            return

        # 2. Context Resolution
        try:
            tenant_id, is_global, filename, storage_path, current_meta = self.resolver.resolve(record)
            self.policy.validate_tenant_isolation(is_global, tenant_id)
            collection_id = self._resolve_collection_id(record=record, metadata=current_meta)
            
            if not storage_path:
                raise ValueError(f"Missing storage_path for document {doc_id}")

            # 3. Atomic Start
            await self.state_manager.start_processing(doc_id, filename, current_meta, tenant_id)

            # 4. Download Phase (Delegated)
            bucket_name = current_meta.get("storage_bucket") if isinstance(current_meta, dict) else None
            source = await self.download_service.download(
                doc_id,
                storage_path,
                filename,
                tenant_id,
                bucket_name=bucket_name,
            )
            
            try:
                # 5. Metadata & Strategy Resolution
                ingestion_metadata = self.metadata_adapter.map_to_domain(
                    record=record,
                    metadata=current_meta,
                    source_id=doc_id,
                    filename=filename,
                    is_global=is_global,
                    institution_id=tenant_id
                )
                strategy_key = (
                    current_meta.get("force_strategy") 
                    or (ingestion_metadata.metadata or {}).get("strategy_override") 
                    or await self.taxonomy_manager.resolve_strategy_slug(ingestion_metadata.type_id)
                )

                # 6. Dispatch to Strategy
                await self.state_manager.log_step(doc_id, "Ejecutando pipeline de ingesta...", tenant_id=tenant_id)
                
                # Cleanup previous data for idempotency
                await self.content_repo.delete_chunks_by_source_id(doc_id)

                result = await self.dispatcher.dispatch(
                    source=source, 
                    metadata=ingestion_metadata, 
                    strategy_key=strategy_key,
                    source_id=doc_id
                )

                if result.status != IngestionStatus.SUCCESS.value:
                    raise RuntimeError(
                        f"Ingestion pipeline returned non-success status='{result.status}' for doc_id={doc_id}"
                    )

                if result.chunks_count <= 0 or not result.chunks:
                    raise RuntimeError(
                        f"Ingestion produced zero chunks for doc_id={doc_id}; refusing to mark as processed"
                    )

                # 7. Persistence Phase
                if result.chunks:
                    self._attach_collection_scope_to_chunks(result.chunks, collection_id)
                    self._ensure_chunk_ids(result.chunks)
                    await self.state_manager.log_step(doc_id, f"Persistiendo {len(result.chunks)} fragmentos...", tenant_id=tenant_id)
                    persisted_count = await self.content_repo.save_chunks(result.chunks)
                    
                    if persisted_count <= 0:
                        raise RuntimeError(f"Audit Persistence Failure: strategies produced chunks but repository persisted 0 for doc_id={doc_id}")

                    # 8. Visual Anchor stitching (strict for visual pages)
                    visual_stats = await self._run_visual_anchor_if_needed(
                        doc_id=doc_id,
                        tenant_id=tenant_id,
                        result=result,
                    )
                    if visual_stats.get("attempted", 0) > 0:
                        current_meta["visual_anchor"] = visual_stats
                        loss_events = int(visual_stats.get("degraded_inline", 0)) + int(
                            visual_stats.get("parse_failed", 0)
                        ) + int(visual_stats.get("skipped", 0))
                        current_meta["visual_loss_indicator"] = {
                            "has_loss": loss_events > 0,
                            "loss_events": loss_events,
                            "copyright_blocks": int(visual_stats.get("parse_failed_copyright", 0)),
                        }

                    # 9. RAPTOR Processing (Optional)
                    await self._run_raptor_if_needed(doc_id, tenant_id, result, collection_id=collection_id)

                    # 10. Graph Enrichment (Optional)
                    await self._run_graph_if_needed(doc_id, tenant_id, result)

                # 11. Success Handler
                await self.state_manager.handle_success(doc_id, persisted_count, current_meta, tenant_id)

            finally:
                await self._cleanup_source(source, current_meta)

        except Exception as e:
            # 11. Atomic Error Handling
            tenant_safe = record.get('institution_id') or record.get('metadata', {}).get('institution_id')
            await self.state_manager.handle_error(doc_id, e, record.get('metadata', {}), tenant_id=tenant_safe)

    async def _run_visual_anchor_if_needed(self, doc_id: str, tenant_id: Optional[str], result: Any) -> Dict[str, Any]:
        return await self.visual_anchor_service.run_if_needed(doc_id=doc_id, tenant_id=tenant_id, result=result)

    async def _run_raptor_if_needed(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        result: Any,
        collection_id: Optional[str] = None,
    ):
        if self.raptor_processor and len(result.chunks) > 5: # Arbitrary threshold for RAPTOR utility
            await self.state_manager.log_step(doc_id, "Iniciando procesamiento RAPTOR...", tenant_id=tenant_id)
            try:
                from app.domain.raptor_schemas import BaseChunk
                from uuid import uuid4, UUID
                
                # Convert chunks to BaseChunk objects
                base_chunks = []
                for c in result.chunks:
                    # handle both dict and object
                    content = c.get("content") if isinstance(c, dict) else c.content
                    embedding = c.get("embedding") if isinstance(c, dict) else c.embedding
                    # generated chunks might not have IDs yet if not retrieved from DB
                    # but for RAPTOR we just need unique IDs for the tree
                    chunk_id = uuid4() 
                    
                    if content and embedding:
                        base_chunks.append(BaseChunk(
                            id=chunk_id,
                            content=content,
                            embedding=embedding,
                            tenant_id=UUID(tenant_id) if tenant_id else UUID("00000000-0000-0000-0000-000000000000"),
                            source_document_id=UUID(doc_id)
                        ))

                if base_chunks:
                    embedding_mode = self._resolve_embedding_mode_for_raptor(result.chunks)
                    tree_result = await self.raptor_processor.build_tree(
                        base_chunks=base_chunks,
                        tenant_id=UUID(tenant_id) if tenant_id else UUID("00000000-0000-0000-0000-000000000000"),
                        source_document_id=UUID(doc_id),
                        collection_id=UUID(collection_id) if collection_id else None,
                        embedding_mode=embedding_mode,
                    )
                    if collection_id:
                        await self._backfill_raptor_collection_id(doc_id=doc_id, collection_id=collection_id)
                    await self.state_manager.log_step(doc_id, f"RAPTOR completado: {tree_result.total_nodes_created} nodos resumen.", "SUCCESS", tenant_id=tenant_id)
                    
            except Exception as e:
                logger.error(f"RAPTOR processing failed for {doc_id}: {e}")
                await self.state_manager.log_step(doc_id, f"Error en RAPTOR (no fatal): {str(e)}", "WARNING", tenant_id=tenant_id)
                # Do not fail ingestion if RAPTOR fails (graceful degradation)

    async def _run_graph_if_needed(self, doc_id: str, tenant_id: Optional[str], result: Any):
        # Graph Extraction + Strong Grounding Links (non-fatal)
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
        except Exception as e:
            err_text = str(e)
            if "instructor" in err_text.lower() and "required" in err_text.lower():
                logger.warning(f"Graph extraction skipped for {doc_id}: {err_text}")
                await self.state_manager.log_step(
                    doc_id,
                    "GraphRAG omitido: falta dependencia opcional 'instructor' (no fatal).",
                    "WARNING",
                    tenant_id=tenant_id,
                )
                return

            logger.error(f"Graph extraction failed for {doc_id}: {e}")
            await self.state_manager.log_step(doc_id, f"Error en GraphRAG (no fatal): {err_text}", "WARNING", tenant_id=tenant_id)

    async def _cleanup_source(self, source: Any, meta: Dict[str, Any]):
        # Cleanup I/O
        if source:
            await source.close()

    @staticmethod
    def _ensure_chunk_ids(chunks: list[Any]) -> None:
        """
        Ensures each chunk has UUID id so lineage can be persisted.
        """
        for chunk in chunks:
            cid = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
            if not cid:
                new_id = str(uuid4())
                if isinstance(chunk, dict): chunk["id"] = new_id
                else: setattr(chunk, "id", new_id)

    @staticmethod
    def _resolve_collection_id(record: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
        if record.get("collection_id"):
            return str(record.get("collection_id"))

        if metadata.get("collection_id"):
            return str(metadata.get("collection_id"))

        nested = metadata.get("metadata")
        if isinstance(nested, dict) and nested.get("collection_id"):
            return str(nested.get("collection_id"))

        return None

    @staticmethod
    def _attach_collection_scope_to_chunks(chunks: list[Any], collection_id: Optional[str]) -> None:
        if not collection_id:
            return
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk["collection_id"] = collection_id
            metadata = chunk.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("collection_id", collection_id)

    async def _backfill_raptor_collection_id(self, doc_id: str, collection_id: str) -> None:
        client = await get_async_supabase_client()
        await client.table("regulatory_nodes").update({"collection_id": collection_id}).eq("source_document_id", doc_id).execute()

    @staticmethod
    def _resolve_embedding_mode_for_raptor(chunks: list[Any]) -> Optional[str]:
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

    async def _run_graph_enrichment(self, doc_id: str, tenant_id: Optional[str], chunks: list[Any]) -> Dict[str, int]:
        """
        Runs chunk-level graph extraction and persists node->chunk grounding links.
        Non-blocking by design for ingestion resilience.
        """
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

        supabase = await get_async_supabase_client()
        graph_repository = SupabaseGraphRepository(supabase)
        extractor = GraphExtractor(get_llm(temperature=0.0, capability="FORENSIC"))
        embedding_mode = self._resolve_embedding_mode_for_raptor(chunks)

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
