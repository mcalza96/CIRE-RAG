import asyncio
import os
from typing import Any, Dict, Optional

import structlog

from app.ai.generation import get_llm
from app.domain.prompts.institutional import InstitutionalPrompts
from app.infrastructure.settings import settings
from app.domain.schemas import ContentChunk
from app.domain.ingestion.types import IngestionStatus
from app.infrastructure.supabase.repositories.supabase_content_repository import SupabaseContentRepository
from app.infrastructure.supabase.repositories.supabase_source_repository import SupabaseSourceRepository
from app.ai.embeddings import JinaEmbeddingService
from app.infrastructure.document_parsers.pdf_parser import PdfParserService
from langchain_core.messages import HumanMessage, SystemMessage

logger = structlog.get_logger(__name__)


class SecurityContextError(Exception):
    """Raised when indexing is attempted without proper tenant context."""


class InstitutionalOrchestrator:
    def __init__(
        self,
        parser: Optional[PdfParserService] = None,
        chunker: Optional[JinaEmbeddingService] = None,
        content_repo: Optional[SupabaseContentRepository] = None,
        source_repo: Optional[SupabaseSourceRepository] = None,
    ):
        self.parser = parser or PdfParserService()
        self.chunker = chunker or JinaEmbeddingService.get_instance()
        self.content_repo = content_repo or SupabaseContentRepository()
        self.source_repo = source_repo or SupabaseSourceRepository()

        self.parse_window_concurrency = max(
            1,
            min(10, int(getattr(settings, "PARSER_WINDOW_CONCURRENCY", 5))),
        )
        self.parse_window_max_retries = max(
            0,
            int(getattr(settings, "PARSER_WINDOW_MAX_RETRIES", 2)),
        )
        self.parse_window_retry_base_delay_seconds = max(
            0.1,
            float(getattr(settings, "PARSER_WINDOW_RETRY_BASE_DELAY_SECONDS", 0.75)),
        )

    async def ingest_file(self, file_path: str) -> Dict[str, Any]:
        if not file_path or not os.path.exists(file_path):
            return {
                "status": IngestionStatus.FAILED.value,
                "error": f"File not found: {file_path}",
            }

        extraction = self.parser.extract_text_with_page_map(file_path)
        if not extraction:
            return {
                "status": IngestionStatus.FAILED.value,
                "error": "Ingestion failed: parser returned no content",
            }

        return {"raw_text": str(extraction.get("full_text") or "")}

    async def parse_raw_text(self, raw_text: str) -> Dict[str, Any]:
        normalized = str(raw_text or "")
        if not normalized.strip():
            return {"status": IngestionStatus.FAILED.value, "error": "No raw text to parse"}

        window_size = 25000
        overlap = 2000
        windows = self._build_windows(normalized, window_size=window_size, overlap=overlap)

        semaphore = asyncio.Semaphore(self.parse_window_concurrency)
        llm = get_llm(temperature=0)
        system_prompt = InstitutionalPrompts.PARSING_SYSTEM

        async def _process_window(i: int, window: str) -> str:
            user_content = f"CONTENIDO A ESTRUCTURAR (parte {i + 1}/{len(windows)}):\n\n{window}"
            total_attempts = self.parse_window_max_retries + 1
            for attempt in range(1, total_attempts + 1):
                try:
                    async with semaphore:
                        response = await llm.ainvoke(
                            [
                                SystemMessage(content=system_prompt),
                                HumanMessage(content=user_content),
                            ]
                        )
                    return str(response.content)
                except Exception as exc:
                    should_retry = self._is_retryable_parse_error(exc) and attempt < total_attempts
                    if not should_retry:
                        raise RuntimeError(
                            f"Window parsing failed at part {i + 1}/{len(windows)}: {str(exc)}"
                        ) from exc
                    backoff_seconds = self.parse_window_retry_base_delay_seconds * (
                        2 ** (attempt - 1)
                    )
                    logger.info(
                        "parse_window_retry",
                        part=i + 1,
                        total_parts=len(windows),
                        attempt=attempt + 1,
                        total_attempts=total_attempts,
                        backoff_seconds=backoff_seconds,
                    )
                    await asyncio.sleep(backoff_seconds)

            raise RuntimeError(
                f"Window parsing failed at part {i + 1}/{len(windows)}: max retries exceeded"
            )

        try:
            parsed_parts = await asyncio.gather(
                *[_process_window(i, w) for i, w in enumerate(windows)]
            )
            return {"parsed_content": "\n\n".join(parsed_parts)}
        except Exception as exc:
            return {
                "status": IngestionStatus.FAILED.value,
                "error": f"Parsing failed: {str(exc)}",
            }

    async def embed_content(self, content: str) -> Dict[str, Any]:
        normalized = str(content or "")
        if not normalized.strip():
            return {
                "status": IngestionStatus.FAILED.value,
                "error": "No parsed content to embed",
            }

        try:
            chunks = await self.chunker.chunk_and_encode(normalized)
        except Exception as exc:
            return {
                "status": IngestionStatus.FAILED.value,
                "error": f"Embedding failed: {str(exc)}",
            }

        semantic_chunks = []
        for chunk in chunks:
            semantic_chunks.append(
                {
                    "content": chunk["content"],
                    "embedding": chunk["embedding"],
                    "metadata": {
                        "char_start": chunk["char_start"],
                        "char_end": chunk["char_end"],
                    },
                }
            )
        return {"semantic_chunks": semantic_chunks}

    async def index_chunks(
        self,
        *,
        tenant_id: Optional[str],
        document_id: Optional[str],
        chunks_data: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not tenant_id:
            logger.critical("security_index_blocked_missing_tenant")
            raise SecurityContextError("Operation blocked: Missing tenant_id in secure context.")

        if not chunks_data:
            return {"status": IngestionStatus.SUCCESS.value, "indexed_count": 0}

        domain_chunks: list[ContentChunk] = []
        for index, chunk in enumerate(chunks_data):
            domain_chunks.append(
                ContentChunk(
                    source_id=document_id,
                    content=chunk["content"],
                    embedding=chunk["embedding"],
                    chunk_index=index,
                    file_page_number=1,
                    metadata={
                        **chunk["metadata"],
                        "institution_id": tenant_id,
                        "is_global": False,
                    },
                )
            )

        if hasattr(self.content_repo, "save_chunks_sync"):
            await asyncio.to_thread(self.content_repo.save_chunks_sync, domain_chunks)
        else:
            await self.content_repo.save_chunks(domain_chunks)

        current_doc = await self.source_repo.get_by_id(document_id)
        current_meta = current_doc.get("metadata", {}) if current_doc else {}
        current_meta.update(
            {"status": IngestionStatus.SUCCESS.value, "chunks_count": len(domain_chunks)}
        )
        await self.source_repo.update_status_and_metadata(
            document_id,
            IngestionStatus.SUCCESS.value,
            current_meta,
        )
        return {"status": IngestionStatus.SUCCESS.value, "indexed_count": len(domain_chunks)}

    @staticmethod
    def _build_windows(text: str, *, window_size: int, overlap: int) -> list[str]:
        if len(text) <= window_size:
            return [text]

        windows: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + window_size, len(text))
            windows.append(text[start:end])
            start += window_size - overlap
        return windows

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        for attr_name in ("status_code", "status", "http_status"):
            value = getattr(exc, attr_name, None)
            if isinstance(value, int):
                return value
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        return int(code) if isinstance(code, int) else None

    @classmethod
    def _is_retryable_parse_error(cls, exc: Exception) -> bool:
        status_code = cls._extract_status_code(exc)
        if status_code in {429, 500, 502, 503, 504}:
            return True
        message = str(exc).lower()
        retryable_markers = (
            "too many requests",
            "rate limit",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
        )
        return any(marker in message for marker in retryable_markers)
