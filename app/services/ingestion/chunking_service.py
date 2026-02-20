import structlog
from typing import List, Dict, Any, Optional
from uuid import NAMESPACE_URL, uuid5
from app.services.ingestion.pdf_parser import PdfParserService
from app.services.ingestion.structure_mapper import StructureMapper
from app.schemas.ingestion import IngestionMetadata
from app.core.ai_models import AIModelConfig
from app.services.ingestion.metadata_enricher import enrich_metadata
from app.services.embedding_service import JinaEmbeddingService
from pydantic import BaseModel, Field, field_validator, model_validator

import re

logger = structlog.get_logger(__name__)


_ISO_DOC_PATTERN = re.compile(r"\bISO\s*[-:_]?\s*(\d{4,5})\b", re.IGNORECASE)
_NOM_ISO_DOC_PATTERN = re.compile(r"\bNOM\s*[-_ ]?ISO\s*[-_ ]?(\d{4,5})\b", re.IGNORECASE)
_TOC_DOT_LEADER_LINE = re.compile(r"^\s*.+\.{3,}\s*\d+\s*$", re.MULTILINE)
_TOC_CLAUSE_REF = re.compile(r"\b\d+(?:\.\d+){1,4}\b")
_FRONTMATTER_HINTS = (
    "reservados los derechos",
    "all rights reserved",
    "copyright",
    "no podra reproducirse",
    "no podrá reproducirse",
    "iso copyright office",
)


def _infer_document_standards(metadata: IngestionMetadata) -> list[str]:
    candidates: list[str] = []
    nested = metadata.metadata if isinstance(metadata.metadata, dict) else {}
    for key in ("source_standard", "standard", "scope"):
        raw = nested.get(key)
        if isinstance(raw, str) and raw.strip():
            candidates.append(raw.strip())
    raw_many = nested.get("source_standards")
    if isinstance(raw_many, list):
        for item in raw_many:
            if isinstance(item, str) and item.strip():
                candidates.append(item.strip())

    for text in (
        metadata.title,
        str(nested.get("filename") or ""),
        str(nested.get("storage_path") or ""),
    ):
        if not text:
            continue
        for match in _ISO_DOC_PATTERN.findall(text):
            candidates.append(f"ISO {match}")
        for match in _NOM_ISO_DOC_PATTERN.findall(text):
            candidates.append(f"ISO {match}")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        m = re.search(r"\b(?:ISO\s*[-:_]?\s*)?(\d{4,5})\b", item, flags=re.IGNORECASE)
        if m:
            canon = f"ISO {m.group(1)}"
        else:
            canon = item.strip().upper()
        if not canon or canon in seen:
            continue
        seen.add(canon)
        normalized.append(canon)
    return normalized


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
    def __init__(self, parser: PdfParserService):
        self.parser = parser

    def split_text(self, text: str, max_chars: int) -> List[str]:
        """
        Legacy: Splits text into chunks of maximum max_chars.
        Uses a simple character-based split. Kept for backward compatibility.
        """
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    def classify_chunk_role(self, content: str) -> dict[str, Any]:
        return self._classify_chunk_role(content)

    async def chunk_document_with_late_chunking(
        self,
        full_text: str,
        embedding_mode: str,
        embedding_provider: Optional[str] = None,
        max_chars: int = AIModelConfig.MAX_CHARACTERS_PER_CHUNKING_BLOCK,
    ) -> List[Dict[str, Any]]:
        """
        Default chunking strategy for production ingestion.

        Strategy order:
        1) Late Chunking on full document via embedding provider.
        2) Contextual section fallback (inject parent context before embedding).
        """
        if not full_text or not full_text.strip():
            return []

        sections = self.split_by_headings(full_text, max_chars=max_chars)
        chunker = JinaEmbeddingService.get_instance()

        try:
            late_chunks = await chunker.chunk_and_encode(
                full_text,
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
        max_chars: int = AIModelConfig.MAX_CHARACTERS_PER_CHUNKING_BLOCK,
        *,
        skip_structural_embedding: bool = True,
    ) -> List[Dict[str, Any]]:
        if not full_text or not full_text.strip():
            return []
        sections = self.split_by_headings(full_text, max_chars=max_chars)
        chunker = JinaEmbeddingService.get_instance()
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
        chunker: JinaEmbeddingService,
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
        Lightweight global context for contextual retrieval fallback.
        """
        headings = [s.get("heading_path", "") for s in sections if s.get("heading_path")]
        first_headings = " | ".join(headings[:3])
        first_section_excerpt = (
            (sections[0].get("content", "")[:240] if sections else "").replace("\n", " ").strip()
        )
        return f"HEADINGS: {first_headings}\nEXCERPT: {first_section_excerpt}".strip()

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
        """
        Splits markdown by heading boundaries (##, ###, ####), respecting max_chars.

        Returns a list of section dicts:
        [{"content": str, "heading_path": str, "char_start": int, "char_end": int}]

        If a section exceeds max_chars, it is sub-split at paragraph boundaries (\n\n).
        """
        # Split on markdown headings (## and deeper) or Bold numbered headings (e.g. **1. TITLE**)
        heading_pattern = re.compile(r"^(?:#{2,4}|(?:\d+\.)+)\s+(.+)", re.MULTILINE)

        sections: List[Dict[str, Any]] = []
        matches = list(heading_pattern.finditer(markdown_text))

        if not matches:
            # No headings found: fall back to paragraph-based splitting
            return self._split_by_paragraphs(markdown_text, max_chars)

        # Handle text before first heading
        if matches[0].start() > 0:
            preamble = markdown_text[: matches[0].start()].strip()
            if preamble:
                sections.append(
                    {
                        "content": preamble,
                        "heading_path": "[Preámbulo]",
                        "char_start": 0,
                        "char_end": matches[0].start(),
                    }
                )

        # Process each heading section
        heading_stack: List[str] = []

        for i, match in enumerate(matches):
            level = 2  # Default for numbered headings
            if match.group(0).startswith("#"):
                hash_match = re.match(r"^#+", match.group(0))
                if hash_match:
                    level = len(hash_match.group(0))

            title = match.group(1).strip()

            # Build heading path (breadcrumb)
            # Trim stack to current level
            heading_stack = heading_stack[: level - 2]  # offset by 2 since ## is level 2
            heading_stack.append(title)
            heading_path = " > ".join(heading_stack)

            # Extract section content (from this heading to next heading)
            section_start = match.start()
            section_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
            section_content = markdown_text[section_start:section_end].strip()

            if not section_content:
                continue

            # If section exceeds max_chars, sub-split at paragraph boundaries
            if len(section_content) > max_chars:
                sub_sections = self._split_long_section(
                    section_content, heading_path, section_start, max_chars
                )
                sections.extend(sub_sections)
            else:
                sections.append(
                    {
                        "content": section_content,
                        "heading_path": heading_path,
                        "char_start": section_start,
                        "char_end": section_end,
                    }
                )

        logger.info("heading_split_complete", total_sections=len(sections))
        return sections

    def _split_long_section(
        self, content: str, heading_path: str, base_offset: int, max_chars: int
    ) -> List[Dict[str, Any]]:
        """Sub-splits an oversized section at paragraph boundaries."""
        paragraphs = content.split("\n\n")
        chunks: List[Dict[str, Any]] = []
        current_chunk = ""
        chunk_start = base_offset
        part_num = 0

        for para in paragraphs:
            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                part_num += 1
                chunks.append(
                    {
                        "content": current_chunk.strip(),
                        "heading_path": f"{heading_path} (parte {part_num})",
                        "char_start": chunk_start,
                        "char_end": chunk_start + len(current_chunk),
                    }
                )
                chunk_start += len(current_chunk) + 2
                current_chunk = para
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para

        if current_chunk.strip():
            part_num += 1
            chunks.append(
                {
                    "content": current_chunk.strip(),
                    "heading_path": f"{heading_path} (parte {part_num})"
                    if part_num > 1
                    else heading_path,
                    "char_start": chunk_start,
                    "char_end": chunk_start + len(current_chunk),
                }
            )

        return chunks

    def _split_by_paragraphs(self, text: str, max_chars: int) -> List[Dict[str, Any]]:
        """Fallback: split by paragraph boundaries when no headings exist."""
        paragraphs = text.split("\n\n")
        chunks: List[Dict[str, Any]] = []
        current_chunk = ""
        chunk_start = 0

        for para in paragraphs:
            # SAFETY: If a single paragraph is too large, split it by characters
            if len(para) > max_chars:
                sub_paras = [para[i : i + max_chars] for i in range(0, len(para), max_chars)]
                for sp in sub_paras:
                    if current_chunk and len(current_chunk) + len(sp) + 2 > max_chars:
                        chunks.append(
                            {
                                "content": current_chunk.strip(),
                                "heading_path": "",
                                "char_start": chunk_start,
                                "char_end": chunk_start + len(current_chunk),
                            }
                        )
                        chunk_start += len(current_chunk) + 2
                        current_chunk = sp
                    else:
                        current_chunk += ("\n\n" if current_chunk else "") + sp
                continue

            if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
                chunks.append(
                    {
                        "content": current_chunk.strip(),
                        "heading_path": "",
                        "char_start": chunk_start,
                        "char_end": chunk_start + len(current_chunk),
                    }
                )
                chunk_start += len(current_chunk) + 2
                current_chunk = para
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para

        if current_chunk.strip():
            chunks.append(
                {
                    "content": current_chunk.strip(),
                    "heading_path": "",
                    "char_start": chunk_start,
                    "char_end": chunk_start + len(current_chunk),
                }
            )

        return chunks

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

        # 1. Structural Enrichment
        final_content = content
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
            "model": str(profile.get("model") or AIModelConfig.JINA_MODEL_NAME),
            "authority_level": metadata.authority_level.value,
            "embedding_mode": embedding_mode,
            "embedding_provider": str(profile.get("provider") or "jina"),
            "embedding_profile": profile,
            "structure_context": structure_context,
            "section_node_id": section_node_id,
            **role_meta,
        }
        base_metadata.update(enriched_metadata)

        inferred_standards = _infer_document_standards(metadata)
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
        if not isinstance(structure_context, dict):
            return None
        section_ref = str(structure_context.get("section_ref") or "").strip()
        if not section_ref:
            return None
        source_text = str(source_id or "").strip()
        if not source_text:
            return None
        return str(uuid5(NAMESPACE_URL, f"doc-structure:{source_text}:{section_ref}"))

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
