from typing import Dict, Any, Optional
import structlog
from app.domain.ingestion.ports import ISourceRepository
from app.domain.ingestion.ports import IContentRepository
from app.workflows.ingestion.post_processor import PostIngestionPipelineService
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.domain.ingestion.types import IngestionStatus
from app.domain.ingestion.policies import IngestionPolicy
from app.infrastructure.supabase.repositories.taxonomy_repository import TaxonomyRepository
from app.infrastructure.supabase.adapters.metadata_adapter import SupabaseMetadataAdapter
from app.infrastructure.filesystem.storage import StorageService
from app.infrastructure.observability.correlation import set_correlation_id
from app.infrastructure.state_management.context_resolver import IngestionContextResolver
from app.infrastructure.observability.logger_config import bind_context
from app.infrastructure.supabase.repositories.supabase_raptor_repository import SupabaseRaptorRepository
from app.infrastructure.network.downloader import DocumentDownloadService
from app.infrastructure.state_management.state_manager import IngestionStateManager
from app.domain.ingestion.anchors.anchor_service import VisualAnchorService
from app.infrastructure.document_parsers.visual_parser import VisualDocumentParser
from app.workflows.ingestion.integrator import VisualGraphIntegrator
from app.infrastructure.settings import settings

logger = structlog.get_logger(__name__)

class DocumentProcessor:
    """
    Workflow orchestrator for document ingestion and enrichment.
    Formerly ProcessDocumentWorkerUseCase.
    """
    def __init__(
        self,
        repository: ISourceRepository,
        content_repo: IContentRepository,
        storage_service: StorageService,
        dispatcher: IngestionDispatcher,
        taxonomy_manager: TaxonomyRepository,
        metadata_adapter: SupabaseMetadataAdapter,
        policy: IngestionPolicy,
        resolver: Optional[IngestionContextResolver] = None,
        raptor_processor: Optional[Any] = None,
        visual_parser: Optional[VisualDocumentParser] = None,
        visual_integrator: Optional[VisualGraphIntegrator] = None,
        download_service: Optional[DocumentDownloadService] = None,
        state_manager: Optional[IngestionStateManager] = None,
        raptor_repo: Optional[SupabaseRaptorRepository] = None,
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

        self.download_service = download_service or DocumentDownloadService(
            storage_service, repository
        )
        self.state_manager = state_manager or IngestionStateManager(repository)

        resolved_raptor_repo = raptor_repo or SupabaseRaptorRepository()
        self.visual_anchor_service = self._build_visual_anchor_service()
        self.post_ingestion_service = self._build_post_ingestion_service(resolved_raptor_repo)

    async def process(self, record: Dict[str, Any]):
        """Executes the ingestion pipeline for a given document record."""
        # 0. Context Recovery & Observability
        doc_id, correlation_id, course_id = self.resolver.extract_observability_context(record)
        if not doc_id:
            logger.error(f"[DocumentProcessor] Record missing ID: {record}")
            return

        set_correlation_id(correlation_id)
        bind_context(course_id=course_id)

        logger.info(
            "Ingestión de documento iniciada",
            doc_id=doc_id,
            correlation_id=correlation_id,
            course_id=course_id,
        )

        # 1. Domain Policy Guard
        current_status = record.get("status")
        meta: Dict[str, Any] = record.get("metadata", {}) or {}
        if not self.policy.should_process(
            str(current_status or ""), str(meta.get("status") or ""), metadata=meta
        ):
            logger.debug(f"[DocumentProcessor] Document {doc_id} ignored by policy.")
            return

        # 2. Context Resolution
        try:
            tenant_id, is_global, filename, storage_path, current_meta = self.resolver.resolve(
                record
            )
            self.policy.validate_tenant_isolation(is_global, tenant_id or "")
            collection_id = self._resolve_collection_id(record=record, metadata=current_meta)

            if not storage_path:
                raise ValueError(f"Missing storage_path for document {doc_id}")

            # 3. Atomic Start
            await self.state_manager.start_processing(doc_id, filename, current_meta, tenant_id)

            # 4. Download Phase (Delegated)
            bucket_name = (
                current_meta.get("storage_bucket") if isinstance(current_meta, dict) else None
            )
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
                    institution_id=tenant_id,
                )
                strategy_key = (
                    current_meta.get("force_strategy")
                    or (ingestion_metadata.metadata or {}).get("strategy_override")
                    or await self.taxonomy_manager.resolve_strategy_slug(
                        str(ingestion_metadata.type_id or "")
                    )
                )

                # 6. Dispatch to Strategy
                await self.state_manager.log_step(
                    doc_id, "Ejecutando pipeline de ingesta...", tenant_id=tenant_id
                )

                # Cleanup previous data for idempotency
                await self.content_repo.delete_chunks_by_source_id(doc_id)

                result = await self.dispatcher.dispatch(
                    source=source,
                    metadata=ingestion_metadata,
                    strategy_key=strategy_key,
                    source_id=doc_id,
                )

                result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
                routing_meta = (
                    result_metadata.get("routing")
                    if isinstance(result_metadata.get("routing"), dict)
                    else {}
                )
                if routing_meta:
                    current_meta["routing"] = routing_meta

                if result.status != IngestionStatus.SUCCESS.value:
                    raise RuntimeError(
                        f"Ingestion pipeline returned non-success status='{result.status}' for doc_id={doc_id}"
                    )

                chunks_count = int(result.chunks_count or 0)
                if chunks_count <= 0 or not result.chunks:
                    raise RuntimeError(
                        f"Ingestion produced zero chunks for doc_id={doc_id}; refusing to mark as processed"
                    )

                # 7. Persistence Phase
                persisted_count = 0
                if result.chunks:
                    persisted_count = await self.post_ingestion_service.persist_chunks(
                        doc_id=doc_id,
                        tenant_id=tenant_id,
                        chunks=result.chunks,
                        collection_id=collection_id,
                    )

                    visual_async_enabled = bool(
                        getattr(settings, "INGESTION_VISUAL_ASYNC_ENABLED", True)
                    )
                    if not visual_async_enabled:
                        visual_stats = await self._run_visual_anchor_if_needed(
                            doc_id=doc_id,
                            tenant_id=tenant_id,
                            result=result,
                        )
                        if visual_stats.get("attempted", 0) > 0:
                            current_meta["visual_anchor"] = visual_stats
                            loss_events = (
                                int(visual_stats.get("degraded_inline", 0))
                                + int(visual_stats.get("parse_failed", 0))
                                + int(visual_stats.get("skipped", 0))
                            )
                            current_meta["visual_loss_indicator"] = {
                                "has_loss": loss_events > 0,
                                "loss_events": loss_events,
                                "copyright_blocks": int(
                                    visual_stats.get("parse_failed_copyright", 0)
                                ),
                            }
                    else:
                        current_meta["visual_anchor"] = {
                            "status": "queued",
                            "async": True,
                            "attempted": 0,
                            "stitched": 0,
                        }

                    if bool(getattr(settings, "INGESTION_ENRICHMENT_ASYNC_ENABLED", True)):
                        try:
                            enqueued = (
                                await self.post_ingestion_service.enqueue_deferred_enrichment(
                                    doc_id=doc_id,
                                    tenant_id=tenant_id,
                                    collection_id=collection_id,
                                    include_visual=visual_async_enabled,
                                    include_graph=True,
                                    include_raptor=True,
                                )
                            )
                            current_meta["enrichment"] = {
                                "status": "queued" if enqueued else "already_queued",
                                "async": True,
                            }
                            current_meta["searchable"] = {
                                "status": "ready",
                                "chunks_persisted": int(persisted_count),
                            }
                            await self.state_manager.log_step(
                                doc_id,
                                (
                                    "Enriquecimiento post-ingesta encolado "
                                    "(GraphRAG + RAPTOR en background)."
                                ),
                                "INFO",
                                tenant_id=tenant_id,
                            )
                        except Exception as exc:
                            logger.warning(
                                "deferred_enrichment_enqueue_failed",
                                doc_id=doc_id,
                                error=str(exc),
                            )
                            current_meta["enrichment"] = {
                                "status": "enqueue_failed",
                                "async": True,
                                "error": str(exc),
                            }
                            await self.state_manager.log_step(
                                doc_id,
                                "No se pudo encolar enriquecimiento diferido (no fatal).",
                                "WARNING",
                                tenant_id=tenant_id,
                            )
                    else:
                        # 9. RAPTOR Processing (Optional)
                        await self.post_ingestion_service.run_raptor_if_needed(
                            doc_id=doc_id,
                            tenant_id=tenant_id,
                            result=result,
                            collection_id=collection_id,
                        )

                        # 10. Graph Enrichment (Optional)
                        await self.post_ingestion_service.run_graph_if_needed(
                            doc_id=doc_id,
                            tenant_id=tenant_id,
                            result=result,
                        )

                # 11. Success Handler
                await self.state_manager.handle_success(
                    doc_id, persisted_count, current_meta, tenant_id
                )

            finally:
                await self.post_ingestion_service.cleanup_source(source)

        except Exception as e:
            # 11. Atomic Error Handling
            tenant_safe = record.get("institution_id") or record.get("metadata", {}).get(
                "institution_id"
            )
            await self.state_manager.handle_error(
                doc_id, e, record.get("metadata", {}), tenant_id=tenant_safe
            )

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

    def _build_visual_anchor_service(self) -> VisualAnchorService:
        return VisualAnchorService(
            state_manager=self.state_manager,
            visual_parser=self.visual_parser,
            visual_integrator=self.visual_integrator,
        )

    def _build_post_ingestion_service(
        self, raptor_repo: SupabaseRaptorRepository
    ) -> PostIngestionPipelineService:
        return PostIngestionPipelineService(
            content_repo=self.content_repo,
            state_manager=self.state_manager,
            raptor_processor=self.raptor_processor,
            raptor_repo=raptor_repo,
            visual_anchor_service=self.visual_anchor_service,
        )

    async def _run_visual_anchor_if_needed(
        self, doc_id: str, tenant_id: Optional[str], result: Any
    ) -> Dict[str, Any]:
        self.visual_anchor_service.visual_parser = self.visual_parser
        self.visual_anchor_service.visual_integrator = self.visual_integrator
        return await self.visual_anchor_service.run_if_needed(
            doc_id=doc_id, tenant_id=tenant_id, result=result
        )
