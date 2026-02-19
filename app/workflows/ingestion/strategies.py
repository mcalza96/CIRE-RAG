import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from app.core.ai_models import AIModelConfig
from app.schemas.ingestion import IngestionMetadata
from app.domain.models.ingestion_source import IngestionSource
from app.domain.types.ingestion_status import IngestionStatus
from app.core.registry import register_strategy
from app.services.ingestion.structure_mapper import StructureMapper
from app.infrastructure.container import CognitiveContainer
from app.services.ingestion.pdf_parser import PdfParserService
from app.services.ingestion.toc_discovery import TocDiscoveryService
from app.services.ingestion.chunking_service import ChunkingService
from app.services.ingestion.router import DocumentStructureRouter, IngestionTask, ProcessingStrategy
from app.services.embedding_service import JinaEmbeddingService
from app.core.settings import settings

logger = structlog.get_logger(__name__)


def _get_container() -> CognitiveContainer:
    return CognitiveContainer.get_instance()


@dataclass
class IngestionResult:
    """Standardized output from any ingestion strategy."""

    source_id: str
    chunks_count: int
    status: str = IngestionStatus.SUCCESS.value
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunks: List[Dict[str, Any]] = field(default_factory=list)  # New field to carry chunks


class IngestionStrategy(ABC):
    """
    Abstract Base Class for Ingestion Strategies.
    Defines the contract for processing different types of documents.
    """

    @abstractmethod
    async def process(
        self, source: IngestionSource, metadata: IngestionMetadata
    ) -> IngestionResult:
        """
        Process the file and return ingestion results.
        Pure transformation: Source -> Chunks.
        """
        pass


@register_strategy("CONTENT")
class CurriculumContentStrategy(IngestionStrategy):
    """
    Strategy for processing Curriculum Content using JinaLateChunker.
    """

    async def process(
        self, source: IngestionSource, metadata: IngestionMetadata
    ) -> IngestionResult:
        filename = source.get_filename()
        logger.info("starting_content_ingestion", strategy=self.__class__.__name__, file=filename)

        container = _get_container()
        parser: PdfParserService = container.pdf_parser_service
        router: DocumentStructureRouter = container.document_structure_router
        toc_service: TocDiscoveryService = container.toc_discovery_service

        # 1. Route pages with fast heuristics (TEXT_STANDARD vs VISUAL_COMPLEX)
        file_path = source.get_file_path()
        if not file_path:
            raise ValueError("CurriculumContentStrategy requires a local file path.")

        source_path = None
        if metadata.metadata and isinstance(metadata.metadata, dict):
            source_path = metadata.metadata.get("storage_path") or metadata.metadata.get(
                "source_path"
            )
        if not source_path:
            candidate = source.get_filename()
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                source_path = candidate

        if str(settings.INGEST_PARSER_MODE or "local").strip().lower() == "cloud":
            extraction = await parser.extract_structured_document(
                file_path=file_path, source_path=source_path
            )
            if extraction and extraction.get("full_text"):
                full_text = extraction["full_text"]
                page_map = extraction.get(
                    "page_map", [{"page": 1, "start": 0, "end": len(full_text)}]
                )
                page_tasks = []
                text_tasks = []
                visual_tasks = []
            else:
                page_tasks = router.route_document(file_path)
                text_tasks = [
                    task
                    for task in page_tasks
                    if task.strategy == ProcessingStrategy.TEXT_STANDARD
                    and task.raw_content.strip()
                ]
                visual_tasks = [
                    task
                    for task in page_tasks
                    if task.strategy == ProcessingStrategy.VISUAL_COMPLEX
                ]
                full_text, page_map = self._build_text_payload(text_tasks)
        else:
            page_tasks = router.route_document(file_path)
            text_tasks = [
                task
                for task in page_tasks
                if task.strategy == ProcessingStrategy.TEXT_STANDARD and task.raw_content.strip()
            ]
            visual_tasks = [
                task for task in page_tasks if task.strategy == ProcessingStrategy.VISUAL_COMPLEX
            ]
            full_text, page_map = self._build_text_payload(text_tasks)

        if not full_text:
            logger.warning("empty_file_detected", file=filename)
            return IngestionResult(
                source_id=metadata.source_id,
                chunks_count=0,
                status=IngestionStatus.EMPTY_FILE.value,
            )

        logger.info(
            "document_routed_for_ingestion",
            chars=len(full_text),
            text_pages=len(text_tasks),
            visual_pages=len(visual_tasks),
        )

        # 2. ToC Discovery (SRP: Delegated to TocService)
        toc_structure = None
        toc_entries = None
        try:
            toc_result = toc_service.discover_toc(file_path)
            if toc_result and toc_result.has_structure:
                logger.info("toc_discovered", entries_count=len(toc_result.entries))
                toc_structure = toc_result.dict()
                toc_entries = toc_result.entries
        except Exception as e:
            logger.warning("toc_discovery_failed_fail_open", error=str(e))

        structure_mapper = StructureMapper(toc_entries)

        # 3. Late Chunking (default) + contextual fallback
        embedding_mode = (
            metadata.metadata.get("embedding_mode", settings.JINA_MODE)
            if metadata.metadata
            else settings.JINA_MODE
        )
        embedding_provider = (
            metadata.metadata.get("embedding_provider")
            if metadata.metadata and isinstance(metadata.metadata, dict)
            else None
        )
        embedding_engine = JinaEmbeddingService.get_instance()
        embedding_profile = embedding_engine.resolve_ingestion_profile(metadata.metadata)
        embedding_provider_applied = str(embedding_profile.get("provider") or "jina")
        logger.info(
            "embedding_provider_applied",
            provider=embedding_provider_applied,
            model=embedding_profile.get("model"),
            source_id=metadata.source_id,
        )
        chunking_service = ChunkingService(parser)

        late_chunks = await chunking_service.chunk_document_with_late_chunking(
            full_text=full_text,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider_applied,
            max_chars=AIModelConfig.MAX_CHARACTERS_PER_CHUNKING_BLOCK,
        )

        all_chunks_data = []
        for chunk in late_chunks:
            chunk_data = chunking_service.assemble_chunk(
                content=chunk["content"],
                char_start=chunk["char_start"],
                char_end=chunk["char_end"],
                page_map=page_map,
                metadata=metadata,
                embedding=chunk["embedding"],
                strategy_name="late_chunking_v3",
                embedding_mode=embedding_mode,
                embedding_profile=embedding_profile,
                structure_mapper=structure_mapper,
            )
            # Enrich with heading path
            if chunk.get("heading_path"):
                chunk_data["metadata"] = chunk_data.get("metadata", {})
                chunk_data["metadata"]["heading_path"] = chunk["heading_path"]
            all_chunks_data.append(chunk_data)

        logger.info("chunking_complete", total_chunks=len(all_chunks_data))
        return IngestionResult(
            source_id=metadata.source_id,
            chunks_count=len(all_chunks_data),
            metadata={
                "parser": "hybrid_router_text_extraction",
                "embedding_provider": embedding_profile.get("provider"),
                "embedding_provider_applied": embedding_provider_applied,
                "embedding_model": embedding_profile.get("model"),
                "embedding_dimensions": embedding_profile.get("dimensions"),
                "embedding_profile": embedding_profile,
                "strategy_type": "late_chunking",
                "toc_structure": toc_structure,
                "routing": {
                    "total_pages": len(page_tasks),
                    "text_pages": len(text_tasks),
                    "visual_pages": len(visual_tasks),
                    "visual_tasks": [
                        {
                            "page": task.page_number,
                            "content_type": task.content_type,
                            "image_path": task.raw_content,
                            "metadata": {
                                **(task.metadata or {}),
                                "embedding_mode": embedding_mode,
                                "embedding_provider": embedding_profile.get("provider"),
                                "embedding_provider_applied": embedding_provider_applied,
                                "embedding_profile": embedding_profile,
                            },
                        }
                        for task in visual_tasks
                    ],
                },
            },
            chunks=all_chunks_data,
        )

    @staticmethod
    def _build_text_payload(text_tasks: List[IngestionTask]) -> tuple[str, List[Dict[str, int]]]:
        """Build `full_text` and `page_map` from routed text tasks."""

        full_text_parts: List[str] = []
        page_map: List[Dict[str, int]] = []
        current_char = 0

        for task in text_tasks:
            page_text = task.raw_content.strip()
            if not page_text:
                continue

            start = current_char
            end = start + len(page_text)
            page_map.append({"page": task.page_number, "start": start, "end": end})

            full_text_parts.append(page_text)
            current_char = end + 2

        return "\n\n".join(full_text_parts), page_map


@register_strategy("FAST_CONTENT")
class FastCurriculumContentStrategy(CurriculumContentStrategy):
    """
    Strategy for processing Curriculum Content using Fast Chunking (Recursive/Fixed) + Standard Embedding.
    Lower memory footprint and faster than Late Chunking.
    """

    async def process(
        self, source: IngestionSource, metadata: IngestionMetadata
    ) -> IngestionResult:
        filename = source.get_filename()
        logger.info(
            "starting_fast_content_ingestion", strategy=self.__class__.__name__, file=filename
        )

        container = _get_container()
        chunker = JinaEmbeddingService.get_instance()
        parser: PdfParserService = container.pdf_parser_service
        toc_service: TocDiscoveryService = container.toc_discovery_service

        # 1. Extract Text
        file_path = source.get_file_path()
        if not file_path:
            raise ValueError("FastCurriculumContentStrategy requires a local file path.")

        source_path = None
        if metadata.metadata and isinstance(metadata.metadata, dict):
            source_path = metadata.metadata.get("storage_path") or metadata.metadata.get(
                "source_path"
            )
        if not source_path:
            candidate = source.get_filename()
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                source_path = candidate
        extraction = await parser.extract_structured_document(
            file_path=file_path, source_path=source_path
        )
        if not extraction or not extraction.get("full_text"):
            logger.warning("empty_file_detected", file=filename)
            return IngestionResult(
                source_id=metadata.source_id,
                chunks_count=0,
                status=IngestionStatus.EMPTY_FILE.value,
            )

        full_text = extraction["full_text"]
        page_map = extraction["page_map"]
        logger.info("text_extracted", chars=len(full_text), pages=len(page_map))

        # 2. ToC Discovery
        toc_structure = None
        toc_entries = None
        try:
            toc_result = toc_service.discover_toc(file_path)
            if toc_result and toc_result.has_structure:
                logger.info("toc_discovered", entries_count=len(toc_result.entries))
                toc_structure = toc_result.dict()
                toc_entries = toc_result.entries
        except Exception as e:
            logger.warning("toc_discovery_failed", error=str(e))

        structure_mapper = StructureMapper(toc_entries)

        # 3. Fast Chunking & Embedding
        chunk_size = 1000
        chunking_service = ChunkingService(parser)
        text_chunks = chunking_service.split_text(full_text, chunk_size)

        embedding_mode = (
            metadata.metadata.get("embedding_mode", settings.JINA_MODE)
            if metadata.metadata
            else settings.JINA_MODE
        )
        embedding_provider = (
            metadata.metadata.get("embedding_provider")
            if metadata.metadata and isinstance(metadata.metadata, dict)
            else None
        )
        embedding_profile = chunker.resolve_ingestion_profile(metadata.metadata)
        embedding_provider_applied = str(embedding_profile.get("provider") or "jina")
        logger.info(
            "embedding_provider_applied",
            provider=embedding_provider_applied,
            model=embedding_profile.get("model"),
            source_id=metadata.source_id,
        )
        logger.info("requesting_batch_embeddings", count=len(text_chunks), mode=embedding_mode)

        # FIXED: Calling embed_texts which was missing in original implementation
        embeddings = await chunker.embed_texts(
            text_chunks,
            mode=embedding_mode,
            provider=embedding_provider_applied,
        )

        all_chunks_data = []
        current_offset = 0

        for i, (chunk_text, embedding) in enumerate(zip(text_chunks, embeddings)):
            chunk_len = len(chunk_text)
            start = current_offset

            chunk_data = chunking_service.assemble_chunk(
                content=chunk_text,
                char_start=start,
                char_end=start + chunk_len,
                page_map=page_map,
                metadata=metadata,
                embedding=embedding,
                strategy_name="fast_chunking_v1",
                embedding_mode=embedding_mode,
                embedding_profile=embedding_profile,
                structure_mapper=structure_mapper,
            )
            all_chunks_data.append(chunk_data)
            current_offset += chunk_len

        logger.info("fast_chunking_complete", total_chunks=len(all_chunks_data))
        return IngestionResult(
            source_id=metadata.source_id,
            chunks_count=len(all_chunks_data),
            metadata={
                "parser": "PdfParserService",
                "embedding_provider": embedding_profile.get("provider"),
                "embedding_provider_applied": embedding_provider_applied,
                "embedding_model": embedding_profile.get("model"),
                "embedding_dimensions": embedding_profile.get("dimensions"),
                "embedding_profile": embedding_profile,
                "strategy_type": "fast_content",
                "toc_structure": toc_structure,
            },
            chunks=all_chunks_data,
        )


@register_strategy("PRE_PROCESSED")
class PreProcessedContentStrategy(IngestionStrategy):
    """
    Fast-path strategy for pre-processed documents (.md, .txt).
    Skips PDF parsing and ToC discovery entirely.
    Reads plain text directly from the source file, then applies
    the standard chunking + embedding pipeline.
    """

    async def process(
        self, source: IngestionSource, metadata: IngestionMetadata
    ) -> IngestionResult:
        filename = source.get_filename()
        logger.info(
            "starting_pre_processed_ingestion", strategy=self.__class__.__name__, file=filename
        )

        # 1. Read text directly (NO PDF parser)
        file_path = source.get_file_path()
        if file_path:
            with open(file_path, "r", encoding="utf-8") as f:
                full_text = f.read()
        else:
            raw_bytes = await source.get_content()
            full_text = raw_bytes.decode("utf-8")

        if not full_text or not full_text.strip():
            logger.warning("empty_pre_processed_file", file=filename)
            return IngestionResult(
                source_id=metadata.source_id,
                chunks_count=0,
                status=IngestionStatus.EMPTY_FILE.value,
            )

        logger.info("pre_processed_text_loaded", chars=len(full_text))

        # 2. Build page map from sections (not synthetic page 1)
        page_map = [{"page": 1, "start": 0, "end": len(full_text)}]

        # 3. Late Chunking (default) + contextual fallback
        container = _get_container()
        parser: PdfParserService = container.pdf_parser_service
        chunking_service = ChunkingService(parser)

        # 4. Chunk + Embedding (single production path)
        embedding_mode = (
            metadata.metadata.get("embedding_mode", settings.JINA_MODE)
            if metadata.metadata
            else settings.JINA_MODE
        )
        embedding_provider = (
            metadata.metadata.get("embedding_provider")
            if metadata.metadata and isinstance(metadata.metadata, dict)
            else None
        )
        embedding_engine = JinaEmbeddingService.get_instance()
        embedding_profile = embedding_engine.resolve_ingestion_profile(metadata.metadata)
        embedding_provider_applied = str(embedding_profile.get("provider") or "jina")
        logger.info(
            "embedding_provider_applied",
            provider=embedding_provider_applied,
            model=embedding_profile.get("model"),
            source_id=metadata.source_id,
        )
        late_chunks = await chunking_service.chunk_document_with_late_chunking(
            full_text=full_text,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider_applied,
            max_chars=4000,
        )

        if not late_chunks:
            logger.error("pre_processed_chunking_failed_no_chunks", file=filename)
            return IngestionResult(
                source_id=metadata.source_id,
                chunks_count=0,
                status=IngestionStatus.FAILED.value,
            )

        # 5. Assemble chunks with heading metadata
        structure_mapper = StructureMapper(None)
        all_chunks_data = []

        for chunk in late_chunks:
            chunk_data = chunking_service.assemble_chunk(
                content=chunk["content"],
                char_start=chunk["char_start"],
                char_end=chunk["char_end"],
                page_map=page_map,
                metadata=metadata,
                embedding=chunk["embedding"],
                strategy_name="pre_processed_late_chunking_v3",
                embedding_mode=embedding_mode,
                embedding_profile=embedding_profile,
                structure_mapper=structure_mapper,
            )
            # Enrich with heading path for retrieval quality
            if chunk.get("heading_path"):
                chunk_data["metadata"] = chunk_data.get("metadata", {})
                chunk_data["metadata"]["heading_path"] = chunk["heading_path"]
            all_chunks_data.append(chunk_data)

        logger.info("pre_processed_chunking_complete", total_chunks=len(all_chunks_data))
        return IngestionResult(
            source_id=metadata.source_id,
            chunks_count=len(all_chunks_data),
            metadata={
                "parser": "pre_processed_structural",
                "embedding_provider": embedding_profile.get("provider"),
                "embedding_provider_applied": embedding_provider_applied,
                "embedding_model": embedding_profile.get("model"),
                "embedding_dimensions": embedding_profile.get("dimensions"),
                "embedding_profile": embedding_profile,
                "strategy_type": "pre_processed_structural",
                "source_format": filename.rsplit(".", 1)[-1].lower()
                if "." in filename
                else "unknown",
            },
            chunks=all_chunks_data,
        )
