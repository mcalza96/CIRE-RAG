import os
import logging
from typing import Dict, Any, Optional
from uuid import UUID, uuid4

from app.domain.repositories.source_repository import ISourceRepository
from app.domain.repositories.content_repository import IContentRepository
from app.domain.interfaces.storage_service_interface import IStorageService
from app.domain.interfaces.ingestion_dispatcher_interface import IIngestionDispatcher
from app.schemas.ingestion import IngestionMetadata
from app.domain.types.ingestion_status import IngestionStatus
from app.domain.interfaces.ingestion_policy_interface import IIngestionPolicy, RetryAction
from app.domain.interfaces.taxonomy_manager_interface import ITaxonomyManager
from app.domain.interfaces.metadata_adapter import IMetadataAdapter
from app.domain.models.ingestion_source import IngestionSource
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
        storage_service: IStorageService,
        dispatcher: IIngestionDispatcher,
        taxonomy_manager: ITaxonomyManager,
        metadata_adapter: IMetadataAdapter,
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

    async def _run_visual_anchor_if_needed(self, doc_id: str, tenant_id: str, result: Any) -> Dict[str, Any]:
        """Parse and stitch visual tasks after text chunks are persisted."""

        routing = (result.metadata or {}).get("routing", {}) if hasattr(result, "metadata") else {}
        visual_tasks = routing.get("visual_tasks", []) if isinstance(routing, dict) else []
        if not visual_tasks:
            return {
                "attempted": 0,
                "stitched": 0,
                "degraded_inline": 0,
                "parse_failed": 0,
                "skipped": 0,
            }

        page_to_chunk: dict[int, dict[str, Any]] = {}
        fallback_chunk: dict[str, Any] | None = None
        for chunk in result.chunks:
            if not isinstance(chunk, dict):
                continue
            page = chunk.get("file_page_number")
            if isinstance(page, int) and page not in page_to_chunk:
                page_to_chunk[page] = chunk
            if fallback_chunk is None:
                fallback_chunk = chunk

        if fallback_chunk is None:
            raise RuntimeError("Visual anchor stitching requires at least one persisted text chunk.")

        stitched = 0
        degraded_inline = 0
        parse_failed = 0
        parse_failed_copyright = 0
        parse_failed_copyright_refs: list[dict[str, Any]] = []
        skipped = 0
        for task in visual_tasks:
            page = int(task.get("page", 0) or 0)
            content_type = str(task.get("content_type", "table"))
            image_path = task.get("image_path")
            if not isinstance(image_path, str) or not image_path:
                skipped += 1
                continue

            parent_chunk = self._select_parent_chunk(page=page, page_to_chunk=page_to_chunk, fallback_chunk=fallback_chunk)
            parent_chunk_id = parent_chunk.get("id")
            parent_chunk_content = parent_chunk.get("content", "")
            if not parent_chunk_id or not isinstance(parent_chunk_content, str):
                logger.warning(
                    "visual_anchor_invalid_parent_chunk",
                    doc_id=doc_id,
                    page=page,
                )
                skipped += 1
                continue

            # FIX: Verify parent chunk actually exists in DB before calling RPC.
            # Chunks with empty embeddings are filtered out in save_chunks but remain
            # in the in-memory result.chunks array, causing phantom parent references.
            client = await get_async_supabase_client()
            check = await client.table("content_chunks").select("id").eq("id", str(parent_chunk_id)).limit(1).execute()
            if not check.data:
                persisted_parent = await self._resolve_persisted_parent_chunk(client=client, doc_id=doc_id, page=page)
                if persisted_parent is None:
                    logger.warning(
                        "visual_anchor_parent_not_in_db",
                        parent_chunk_id=str(parent_chunk_id),
                        page=page,
                        doc_id=doc_id,
                    )
                    skipped += 1
                    continue

                logger.info(
                    "visual_anchor_parent_rebound",
                    doc_id=doc_id,
                    page=page,
                    old_parent_chunk_id=str(parent_chunk_id),
                    rebound_parent_chunk_id=str(persisted_parent.get("id")),
                )
                parent_chunk_id = str(persisted_parent.get("id"))
                parent_chunk_content = str(persisted_parent.get("content") or "")

            parse_result: Any | None = None

            try:
                task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                raw_mode = task_metadata.get("embedding_mode") or task_metadata.get("jina_mode")
                embedding_mode = str(raw_mode).upper() if raw_mode else None
                if embedding_mode not in {"LOCAL", "CLOUD"}:
                    embedding_mode = None

                parse_result = await self.visual_parser.parse_image(
                    image_path=image_path,
                    content_type=content_type,
                    source_metadata=task_metadata,
                )

                integration = await self.visual_integrator.integrate_visual_node(
                    parent_chunk_id=str(parent_chunk_id),
                    parent_chunk_text=parent_chunk_content,
                    image_path=image_path,
                    parse_result=parse_result,
                    content_type=content_type,
                    anchor_context={
                        "type": content_type,
                        "short_summary": parse_result.dense_summary,
                    },
                    metadata=task_metadata,
                    embedding_mode=embedding_mode,
                )

                parent_chunk["content"] = parent_chunk_content + "\n\n" + integration.anchor_token
                stitched += 1
            except Exception as integration_exc:
                if parse_result is None:
                    parse_failed += 1
                    err_text = str(integration_exc).lower()
                    if (
                        "gemini_copyright_block" in err_text
                        or ("finish_reason" in err_text and "4" in err_text)
                        or "copyright" in err_text
                    ):
                        parse_failed_copyright += 1
                        image_name = image_path.rsplit("/", 1)[-1] if isinstance(image_path, str) else ""
                        parse_failed_copyright_refs.append(
                            {
                                "page": page,
                                "parent_chunk_id": str(parent_chunk_id),
                                "image": image_name,
                            }
                        )

                logger.warning(
                    "visual_anchor_stitch_failed_inline_fallback",
                    doc_id=doc_id,
                    parent_chunk_id=str(parent_chunk_id),
                    page=page,
                    stage="parse" if parse_result is None else "integrate",
                    error=str(integration_exc),
                )

                fallback_content = self._build_inline_visual_fallback(
                    parent_chunk_content=parent_chunk_content,
                    content_type=content_type,
                    parse_result=parse_result,
                )
                await self._persist_chunk_content_fallback(
                    chunk_id=str(parent_chunk_id),
                    content=fallback_content,
                )
                parent_chunk["content"] = fallback_content
                degraded_inline += 1

        await self.state_manager.log_step(
            doc_id,
            (
                "Visual anchor stitching completado: "
                f"{stitched}/{len(visual_tasks)} nodos visuales, "
                f"fallback_inline={degraded_inline}, "
                f"parse_failed={parse_failed}, "
                f"parse_failed_copyright={parse_failed_copyright}, skipped={skipped}."
            ),
            "SUCCESS",
            tenant_id=tenant_id,
        )
        return {
            "attempted": len(visual_tasks),
            "stitched": stitched,
            "degraded_inline": degraded_inline,
            "parse_failed": parse_failed,
            "parse_failed_copyright": parse_failed_copyright,
            "parse_failed_copyright_refs": parse_failed_copyright_refs,
            "skipped": skipped,
        }

    async def _resolve_persisted_parent_chunk(self, client: Any, doc_id: str, page: int) -> Optional[Dict[str, Any]]:
        """Find a persisted parent chunk when the in-memory candidate was filtered out."""

        if page > 0:
            by_page = (
                await client.table("content_chunks")
                .select("id,content,file_page_number")
                .eq("source_id", doc_id)
                .eq("file_page_number", page)
                .limit(1)
                .execute()
            )
            rows = by_page.data or []
            if rows:
                row = rows[0]
                return row if isinstance(row, dict) else None

        nearest = (
            await client.table("content_chunks")
            .select("id,content,file_page_number")
            .eq("source_id", doc_id)
            .limit(1)
            .execute()
        )
        rows = nearest.data or []
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    async def _persist_chunk_content_fallback(self, chunk_id: str, content: str) -> None:
        """Best-effort persistence fallback when visual node stitching fails."""

        client = await get_async_supabase_client()
        await client.table("content_chunks").update({"content": content}).eq("id", chunk_id).execute()

    @staticmethod
    def _build_inline_visual_fallback(parent_chunk_content: str, content_type: str, parse_result: Any) -> str:
        """Inline fallback to avoid losing table context when node persistence fails."""

        markdown = getattr(parse_result, "markdown_content", "")
        safe_markdown = markdown.strip() if isinstance(markdown, str) else ""
        if not safe_markdown:
            safe_markdown = "[VISUAL_PARSE_UNAVAILABLE]"

        block = (
            f"<visual_fallback type=\"{content_type}\">\n"
            f"{safe_markdown}\n"
            "</visual_fallback>"
        )
        if parent_chunk_content.endswith("\n"):
            return parent_chunk_content + block
        if parent_chunk_content:
            return parent_chunk_content + "\n\n" + block
        return block

    @staticmethod
    def _select_parent_chunk(
        page: int,
        page_to_chunk: dict[int, dict[str, Any]],
        fallback_chunk: dict[str, Any],
    ) -> dict[str, Any]:
        """Select nearest text chunk to anchor a visual node."""

        if page in page_to_chunk:
            return page_to_chunk[page]

        lower_pages = [p for p in page_to_chunk.keys() if p < page]
        if lower_pages:
            return page_to_chunk[max(lower_pages)]

        higher_pages = [p for p in page_to_chunk.keys() if p > page]
        if higher_pages:
            return page_to_chunk[min(higher_pages)]

        return fallback_chunk

    async def _run_raptor_if_needed(
        self,
        doc_id: str,
        tenant_id: str,
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

    async def _run_graph_if_needed(self, doc_id: str, tenant_id: str, result: Any):
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
