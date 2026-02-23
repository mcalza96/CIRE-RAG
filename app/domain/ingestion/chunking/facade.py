import structlog
from typing import List, Dict, Any, Optional
from app.domain.ingestion.ports import IChunkEmbeddingService, IPageLocator
from app.domain.ingestion.chunking.identity_service import ChunkIdentityService
from app.domain.ingestion.chunking.splitter_strategies import (
    RecursiveTextSplitter,
    SemanticHeadingSplitter,
)
from app.domain.ingestion.structure.structure_mapper import StructureMapper
from app.domain.schemas.ingestion_schemas import IngestionMetadata
from app.domain.ingestion.metadata.metadata_enricher import enrich_metadata
from pydantic import BaseModel, Field, field_validator, model_validator

import re

logger = structlog.get_logger(__name__)
DEFAULT_MAX_CHARACTERS_PER_CHUNKING_BLOCK = 6000


_TOC_DOT_LEADER_LINE = re.compile(r"^\s*.+\.{3,}\s*\d+\s*$", re.MULTILINE)
_TOC_CLAUSE_REF = re.compile(r"\b\d+(?:\.\d+){1,4}\b")
_TOC_CLAUSE_LINE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+[A-ZÁÉÍÓÚÑÜ]")
_ISO_FOOTER_RE = re.compile(
    r"(?is)ISO\s*\d{4,5}:\d{4}\s*\(traducci[oó]n\s+oficial\)[\s\S]{0,120}?©\s*ISO\s*\d{4}\s*[−-]\s*Todos\s+los\s+derechos\s+reservados"
)
_ISO_HEADER_RE = re.compile(
    r"(?is)NORMA\s+INTERNACIONAL[\s\S]{0,80}?Traducci[oó]n\s+oficial\s+Official\s+translation\s+Traduction\s+officielle"
)
_FRONTMATTER_HINTS = (
    "reservados los derechos",
    "all rights reserved",
    "copyright",
    "no podra reproducirse",
    "no podrá reproducirse",
    "iso copyright office",
)


class LateChunkResult(BaseModel):
    """
    Validated late/contextual chunk output.
    """

    content: str = Field(min_length=1)
    embedding: Optional[List[float]] = None
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    heading_path: Optional[str] = None

    @field_validator("embedding")
    @classmethod
    def _validate_embedding(cls, value: Optional[List[float]]) -> Optional[List[float]]:
        if value is not None and not value:
            raise ValueError("embedding cannot be empty when present")
        return value

    @model_validator(mode="after")
    def _validate_char_window(self) -> "LateChunkResult":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class ChunkingService:
    def __init__(
        self,
        parser: IPageLocator,
        embedding_service: IChunkEmbeddingService,
        *,
        recursive_splitter: Optional[RecursiveTextSplitter] = None,
        semantic_splitter: Optional[SemanticHeadingSplitter] = None,
        identity_service: Optional[ChunkIdentityService] = None,
    ):
        self.parser = parser
        self.embedding_service = embedding_service
        self.recursive_splitter = recursive_splitter or RecursiveTextSplitter()
        self.semantic_splitter = semantic_splitter or SemanticHeadingSplitter(
            self.recursive_splitter
        )
        self.identity_service = identity_service or ChunkIdentityService()

    def split_text(self, text: str, max_chars: int) -> List[str]:
        """
        Legacy: Splits text into chunks of maximum max_chars.
        Uses a simple character-based split. Kept for backward compatibility.
        """
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    def classify_chunk_role(self, content: str) -> dict[str, Any]:
        return self._classify_chunk_role(content)

    def clean_text_for_chunking(self, text: str) -> str:
        cleaned = self._strip_iso_boilerplate(text)
        cleaned = self._strip_toc_block(cleaned)
        return cleaned

    @staticmethod
    def _strip_toc_block(text: str) -> str:
        """
        Detect and remove the Table of Contents (ToC/Índice) block.

        The ToC lines look IDENTICAL to real headings after boilerplate
        cleaning (e.g. "10.3 Mejora continua"), so the heading regex
        cannot distinguish them.  We identify the ToC by its unique
        structural signature: a *dense cluster* of short clause-like
        lines with no body text in between, located in the first quarter
        of the document.
        """
        lines = text.split("\n")
        total = len(lines)
        if total < 30:
            return text  # Document too short to have a meaningful ToC

        # Only scan the first 25% of the document for ToC blocks
        scan_limit = max(60, total // 4)

        # ── Pass 1: find a dense cluster of short clause-like lines ──
        toc_start: int | None = None
        toc_end: int | None = None
        run_start: int | None = None
        clause_count = 0

        for i in range(min(scan_limit, total)):
            stripped = lines[i].strip()
            if not stripped:
                continue  # skip blanks; don't break the run

            is_clause_like = bool(_TOC_CLAUSE_LINE.match(stripped)) and len(stripped) < 90
            # Also catch non-clause ToC lines like "Prólogo", "Bibliografía", "Anexo"
            is_toc_keyword = stripped.lower() in (
                "contenido",
                "índice",
                "indice",
                "contents",
                "table of contents",
                "prólogo",
                "prologo",
                "bibliografía",
                "bibliografia",
            ) or stripped.lower().startswith("anexo ")

            if is_clause_like or is_toc_keyword:
                if run_start is None:
                    run_start = i
                clause_count += 1
            else:
                # A line of real body text breaks the run
                if len(stripped) > 90:
                    if clause_count >= 8:
                        toc_start = run_start
                        toc_end = i
                        break
                    run_start = None
                    clause_count = 0
                # Short non-clause lines (e.g. blank-ish noise) don't break

        # End of scan: the cluster might extend to scan_limit
        if toc_start is None and clause_count >= 8 and run_start is not None:
            toc_start = run_start
            toc_end = min(scan_limit, total)

        if toc_start is None or toc_end is None:
            return text

        # ── Pass 2: expand backwards to catch a "Contenido" header ──
        for j in range(max(0, toc_start - 5), toc_start):
            low = lines[j].strip().lower()
            if low in ("contenido", "índice", "indice", "contents", "table of contents"):
                toc_start = j
                break

        logger.info(
            "toc_block_stripped",
            toc_start_line=toc_start,
            toc_end_line=toc_end,
            lines_removed=toc_end - toc_start,
        )

        result = lines[:toc_start] + lines[toc_end:]
        return "\n".join(result)

    @staticmethod
    def _strip_iso_boilerplate(text: str) -> str:
        """Remove repeated ISO legal/frontmatter noise from extracted markdown."""
        cleaned = str(text or "")

        # 1) Cover-page heading hijacking generated by PDF markdown extractor.
        cleaned = re.sub(r"(?im)^#+\s*NORMA\s*$", "", cleaned)
        cleaned = re.sub(r"(?im)^#+\s*INTERNACIONAL\s*$", "", cleaned)
        cleaned = re.sub(r"(?im)^#+\s*Traducci[oó]n\s+oficial.*$", "", cleaned)

        # 2) Repeated running headers/footers across pages.
        cleaned = re.sub(
            r"(?im)^\*?\*?ISO\s+\d{4,5}:\d{4}\s*\(traducci[oó]n\s+oficial\)\*?\*?\s*$",
            "",
            cleaned,
        )
        cleaned = re.sub(r"(?im)^©\s*ISO\s*\d{4}.*$", "", cleaned)
        cleaned = re.sub(r"(?im)^N[uú]mero\s+de\s+referencia.*$", "", cleaned)

        # 3) Full copyright block and boilerplate patterns.
        cleaned = _ISO_FOOTER_RE.sub("", cleaned)
        cleaned = _ISO_HEADER_RE.sub("", cleaned)
        cleaned = re.sub(
            r"(?is)Reservados\s+los\s+derechos\s+de\s+reproducci[oó]n.*?(?:copyright@iso\.org|www\.iso\.org)\s*",
            "",
            cleaned,
        )

        # 4) Remove orphan page-number lines (roman or numeric only).
        cleaned = re.sub(r"(?im)^\s*(?:[ivxlcdm]+|\d+)\s*$", "", cleaned)

        # 5) Normalize resulting whitespace gaps.
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    async def chunk_document_with_late_chunking(
        self,
        full_text: str,
        embedding_mode: str,
        embedding_provider: Optional[str] = None,
        max_chars: int = DEFAULT_MAX_CHARACTERS_PER_CHUNKING_BLOCK,
    ) -> List[Dict[str, Any]]:
        """
        Default chunking strategy for production ingestion.

        Strategy order:
        1) Late Chunking on full document via embedding provider.
        2) Contextual section fallback (inject parent context before embedding).
        """
        if not full_text or not full_text.strip():
            return []

        normalized_text = self.clean_text_for_chunking(full_text)
        if not normalized_text.strip():
            return []

        sections = self.split_by_headings(normalized_text, max_chars=max_chars)
        chunker = self.embedding_service

        try:
            late_chunks = await chunker.chunk_and_encode(
                normalized_text,
                mode=embedding_mode,
                provider=embedding_provider,
            )
            if late_chunks:
                validated = self._attach_headings_and_validate(late_chunks, sections)
                logger.info("late_chunking_applied", chunks=len(validated), mode=embedding_mode)
                return [item.model_dump() for item in validated]
        except Exception as e:
            logger.warning("late_chunking_failed_fallback_to_contextual", error=str(e))

        contextual = await self._contextual_section_chunking(
            sections,
            chunker,
            embedding_mode,
            embedding_provider,
        )
        logger.info("contextual_chunking_applied", chunks=len(contextual), mode=embedding_mode)
        return [item.model_dump() for item in contextual]

    async def chunk_document_contextual_selective(
        self,
        full_text: str,
        embedding_mode: str,
        embedding_provider: Optional[str] = None,
        max_chars: int = DEFAULT_MAX_CHARACTERS_PER_CHUNKING_BLOCK,
        *,
        skip_structural_embedding: bool = True,
    ) -> List[Dict[str, Any]]:
        if not full_text or not full_text.strip():
            return []
        normalized_text = self.clean_text_for_chunking(full_text)
        if not normalized_text.strip():
            return []
        sections = self.split_by_headings(normalized_text, max_chars=max_chars)
        chunker = self.embedding_service
        contextual = await self._contextual_section_chunking(
            sections,
            chunker,
            embedding_mode,
            embedding_provider,
            skip_structural_embedding=skip_structural_embedding,
        )
        return [item.model_dump() for item in contextual]

    def _attach_headings_and_validate(
        self,
        chunks: List[Dict[str, Any]],
        sections: List[Dict[str, Any]],
    ) -> List[LateChunkResult]:
        enriched: List[LateChunkResult] = []
        for chunk in chunks:
            start = int(chunk.get("char_start", 0))
            end = int(chunk.get("char_end", 0))
            heading_path = self._heading_for_range(start, end, sections)

            enriched.append(
                LateChunkResult(
                    content=str(chunk.get("content", "")).strip(),
                    embedding=list(chunk.get("embedding", [])),
                    char_start=start,
                    char_end=end,
                    heading_path=heading_path,
                )
            )
        return enriched

    async def _contextual_section_chunking(
        self,
        sections: List[Dict[str, Any]],
        chunker: IChunkEmbeddingService,
        embedding_mode: str,
        embedding_provider: Optional[str],
        *,
        skip_structural_embedding: bool = False,
    ) -> List[LateChunkResult]:
        if not sections:
            return []

        global_context = self._build_global_context(sections)
        contextual_texts: List[str] = []

        for section in sections:
            contextual_texts.append(
                self._inject_parent_context(
                    content=section["content"],
                    heading_path=section.get("heading_path", ""),
                    global_context=global_context,
                )
            )

        embedding_indices: List[int] = []
        embedding_texts: List[str] = []
        for idx, section in enumerate(sections):
            if not skip_structural_embedding:
                embedding_indices.append(idx)
                embedding_texts.append(contextual_texts[idx])
                continue
            role_meta = self._classify_chunk_role(str(section.get("content", "")))
            if bool(role_meta.get("retrieval_eligible", True)):
                embedding_indices.append(idx)
                embedding_texts.append(contextual_texts[idx])

        embeddings_by_index: Dict[int, List[float]] = {}
        if embedding_texts:
            embeddings = await chunker.embed_texts(
                embedding_texts,
                mode=embedding_mode,
                provider=embedding_provider,
            )
            for idx, embedding in zip(embedding_indices, embeddings):
                embeddings_by_index[idx] = embedding

        results: List[LateChunkResult] = []
        for idx, (section, contextual_text) in enumerate(zip(sections, contextual_texts)):
            results.append(
                LateChunkResult(
                    content=contextual_text,
                    embedding=embeddings_by_index.get(idx),
                    char_start=int(section["char_start"]),
                    char_end=int(section["char_end"]),
                    heading_path=section.get("heading_path"),
                )
            )

        return results

    def _build_global_context(self, sections: List[Dict[str, Any]]) -> str:
        """
        Lightweight *document-level* context prepended to every chunk.

        IMPORTANT: This must NOT include section-specific headings because
        it is shared across ALL chunks.  Including headings like
        "0 Introducción" here caused every chunk's embedding to match
        queries about "introduction", drowning out the actual intro chunk.

        Instead we provide document-identifying info (title/standard)
        plus a structural overview (total sections count).
        """
        # Extract the document title from the first section's content
        first_content = sections[0].get("content", "") if sections else ""
        # Grab only the first meaningful non-empty line as title hint
        title_line = ""
        for line in first_content.split("\n"):
            stripped = line.strip()
            if stripped and len(stripped) > 10:
                title_line = stripped[:200]
                break

        total_sections = len(sections)
        return f"DOC_TITLE: {title_line}\nTOTAL_SECTIONS: {total_sections}".strip()

    def _inject_parent_context(self, content: str, heading_path: str, global_context: str) -> str:
        if not heading_path and not global_context:
            return content
        return (
            "[PARENT_CONTEXT]\n"
            f"{global_context}\n"
            f"SECTION_PATH: {heading_path or '[none]'}\n"
            "[/PARENT_CONTEXT]\n\n"
            f"{content}"
        )

    def _heading_for_range(
        self, char_start: int, char_end: int, sections: List[Dict[str, Any]]
    ) -> str:
        for section in sections:
            section_start = int(section.get("char_start", 0))
            section_end = int(section.get("char_end", 0))
            if section_start <= char_start < section_end or section_start < char_end <= section_end:
                return section.get("heading_path", "")
        return ""

    def split_by_headings(self, markdown_text: str, max_chars: int = 4000) -> List[Dict[str, Any]]:
        sections = self.semantic_splitter.split(markdown_text, max_chars=max_chars)
        logger.info("heading_split_complete", total_sections=len(sections))
        return sections

    def _split_by_paragraphs(self, text: str, max_chars: int) -> List[Dict[str, Any]]:
        return self.recursive_splitter.split(text, max_chars=max_chars)

    def assemble_chunk(
        self,
        content: str,
        char_start: int,
        char_end: int,
        page_map: List[Any],
        metadata: IngestionMetadata,
        embedding: Optional[List[float]],
        strategy_name: str,
        embedding_mode: str,
        embedding_profile: Optional[Dict[str, Any]],
        structure_mapper: StructureMapper,
    ) -> Dict[str, Any]:
        """
        Enriches a raw chunk with page information, breadcrumbs, and institutional metadata.
        """
        page_num = self.parser.get_page_number(char_start, page_map)

        # Metadata Anchoring (ToC)
        structure_data = structure_mapper.map_page_to_context(page_num)
        structure_context = structure_data.get("structure_context", {})
        breadcrumbs = structure_context.get("breadcrumbs")
        section_node_id = self._resolve_section_node_id(
            source_id=metadata.source_id,
            structure_context=structure_context,
        )

        cleaned_content = self._strip_iso_boilerplate(content)

        # 1. Structural Enrichment
        final_content = cleaned_content
        if breadcrumbs:
            final_content = f"[UBICACIÓN: {breadcrumbs}]\n{final_content}"

        role_meta = self._classify_chunk_role(final_content)

        # 2. Semantic Enrichment (Regex)
        # We enrich based on the RAW content to catch patterns, then update metadata/text
        final_content, enriched_metadata = enrich_metadata(
            final_content,
            {},
            allow_clause_extraction=bool(role_meta.get("retrieval_eligible", True)),
        )

        # Merge basic metadata
        profile = embedding_profile if isinstance(embedding_profile, dict) else {}
        base_metadata = {
            "char_start": char_start,
            "char_end": char_end,
            "strategy": strategy_name,
            "model": str(profile.get("model") or "jina-embeddings-v3"),
            "authority_level": metadata.authority_level.value,
            "embedding_mode": embedding_mode,
            "embedding_provider": str(profile.get("provider") or "jina"),
            "embedding_profile": profile,
            "structure_context": structure_context,
            "section_node_id": section_node_id,
            **role_meta,
        }
        base_metadata.update(enriched_metadata)

        inferred_standards = self.identity_service.infer_document_standards(metadata)
        if inferred_standards and not str(base_metadata.get("source_standard") or "").strip():
            base_metadata["source_standard"] = inferred_standards[0]
            base_metadata.setdefault("scope", inferred_standards[0])
            base_metadata.setdefault("standard", inferred_standards[0])
            if len(inferred_standards) > 1:
                base_metadata["source_standards"] = inferred_standards

        return {
            "source_id": str(metadata.source_id) if metadata.source_id else None,
            "content": final_content,
            "file_page_number": page_num,
            "institution_id": str(metadata.institution_id) if metadata.institution_id else None,
            "is_global": metadata.is_global,
            "embedding": embedding,
            "metadata": base_metadata,
        }

    @staticmethod
    def _resolve_section_node_id(source_id: Any, structure_context: dict[str, Any]) -> str | None:
        return ChunkIdentityService.resolve_section_node_id(source_id, structure_context)

    @staticmethod
    def _classify_chunk_role(content: str) -> dict[str, Any]:
        text = str(content or "")
        lowered = text.lower()
        dot_leader_lines = len(_TOC_DOT_LEADER_LINE.findall(text))
        clause_refs = len(_TOC_CLAUSE_REF.findall(text))
        frontmatter_hits = sum(1 for marker in _FRONTMATTER_HINTS if marker in lowered)
        has_toc_keyword = any(
            marker in lowered
            for marker in (
                "table of contents",
                "indice",
                "índice",
                "contenido",
                "contents",
            )
        )

        is_frontmatter = frontmatter_hits >= 1 and len(text) <= 2200
        is_toc = bool(
            has_toc_keyword or dot_leader_lines >= 2 or (dot_leader_lines >= 1 and clause_refs >= 6)
        )

        if is_frontmatter:
            chunk_role = "frontmatter"
        elif is_toc:
            chunk_role = "toc"
        else:
            chunk_role = "normative_body"

        retrieval_eligible = chunk_role == "normative_body"
        structure_eligible = chunk_role == "toc"
        return {
            "chunk_role": chunk_role,
            "doc_section_type": chunk_role,
            "is_toc": chunk_role == "toc",
            "is_frontmatter": chunk_role == "frontmatter",
            "is_normative_body": chunk_role == "normative_body",
            "retrieval_eligible": retrieval_eligible,
            "structure_eligible": structure_eligible,
            "structural_noise_signals": {
                "dot_leader_lines": dot_leader_lines,
                "clause_refs": clause_refs,
                "frontmatter_hits": frontmatter_hits,
                "has_toc_keyword": has_toc_keyword,
            },
        }
