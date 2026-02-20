"""LLM semantic triple extractor for dense knowledge graphs."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.core.settings import settings
from app.core.structured_generation import StrictEngine, get_strict_engine
from app.core.observability.ingestion_logging import compact_error

try:
    from app.domain.graph_schemas import ExtractedNode, ExtractedEdge, GraphExtractionResult
except Exception:  # pragma: no cover - fallback for older branches

    class ExtractedNode(BaseModel):
        temp_id: str
        name: str
        node_type: str
        content: str
        properties: dict = Field(default_factory=dict)

    class ExtractedEdge(BaseModel):
        source_temp_id: str
        target_temp_id: str
        edge_type: str
        description: str = ""
        weight: float = 1.0

    class GraphExtractionResult(BaseModel):
        nodes: list[ExtractedNode] = Field(default_factory=list)
        edges: list[ExtractedEdge] = Field(default_factory=list)

        def is_empty(self) -> bool:
            return not self.nodes and not self.edges


logger = logging.getLogger(__name__)


class Entity(BaseModel):
    name: str = Field(..., description="Canonical entity name")
    type: str = Field(..., description="Entity type (DOCUMENT, PERSON, LAW, EVENT, etc.)")
    description: str = Field(..., description="Context-aware description for semantic retrieval")

    @field_validator("name", "type", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value


class Relation(BaseModel):
    source: str = Field(..., description="Source entity name")
    target: str = Field(..., description="Target entity name")
    relation_type: str = Field(
        ..., description="Semantic relation type (SIGNED_BY, VIOLATES, etc.)"
    )
    description: str = Field(..., description="Evidence/context supporting this relation")
    weight: int = Field(..., ge=1, le=10, description="Strength score from 1 to 10")

    @field_validator("source", "target", "relation_type", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value


class ChunkGraphExtraction(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.entities and not self.relations


class IndexedChunkGraphExtraction(BaseModel):
    chunk_index: int = Field(..., ge=1, description="1-based chunk index in batch prompt")
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


class BatchChunkGraphExtraction(BaseModel):
    chunks: list[IndexedChunkGraphExtraction] = Field(default_factory=list)


@runtime_checkable
class IGraphExtractor(Protocol):
    def extract(self, text: str, chunk_id: Optional[UUID] = None) -> GraphExtractionResult: ...

    async def extract_async(
        self, text: str, chunk_id: Optional[UUID] = None
    ) -> GraphExtractionResult: ...

    def extract_graph_from_chunk(self, text: str) -> ChunkGraphExtraction: ...


class GraphExtractor(IGraphExtractor):
    """Extracts entities/relations as semantic triples from a chunk."""

    _SYSTEM_PROMPT = (
        "You are an expert semantic information extraction system for GraphRAG. "
        "Extract a dense knowledge graph from the chunk. "
        "Return only structured data matching the schema.\n\n"
        "Rules:\n"
        "1) Extract concrete entities (people, organizations, documents, laws, events, obligations, risks, etc.).\n"
        "2) Use canonical entity names in source/target relation fields, reusing exact names from entities.\n"
        "3) relation_type must be UPPER_SNAKE_CASE and semantically precise (e.g., SIGNED_BY, OWNS, VIOLATES, REQUIRES).\n"
        "4) description fields must include factual context from the text, no speculation.\n"
        "5) weight is an integer 1-10 where 10 means very strong explicit evidence.\n"
        "6) Avoid duplicates and avoid self-relations unless explicitly meaningful in the text.\n"
        "7) For ISO/normative text, preserve exact names for mandatory documents and review inputs/outputs.\n"
        "8) Prefer these relation types when explicit in text: REQUIRES_DOCUMENT, HAS_REVIEW_INPUT, HAS_REVIEW_OUTPUT, BELONGS_TO_CLAUSE.\n"
        "9) If the chunk has no extractable graph facts, return empty entities and relations arrays."
    )

    _USER_PROMPT = (
        "Extract semantic triples from the following text chunk.\nText chunk:\n---\n{text}\n---"
    )

    _USER_BATCH_PROMPT = (
        "Extract semantic triples from the following batch of chunks.\n"
        "Return one output object per chunk using the same chunk_index values.\n"
        "If a chunk has no extractable facts, return empty entities and relations for that chunk.\n"
        "Chunks:\n"
        "---\n"
        "{batch_text}\n"
        "---"
    )

    def __init__(
        self,
        llm: Optional[BaseChatModel] = None,
        strict_engine: Optional[StrictEngine] = None,
    ):
        self._llm = llm
        self._strict_engine = strict_engine or get_strict_engine()
        self._json_fallback_parser = PydanticOutputParser(pydantic_object=ChunkGraphExtraction)

    @staticmethod
    def _empty_result() -> GraphExtractionResult:
        return GraphExtractionResult(nodes=[], edges=[])

    @staticmethod
    def _normalize_relation_type(value: str) -> str:
        return value.strip().replace(" ", "_").replace("-", "_").upper()

    @staticmethod
    def _attach_chunk_grounding(
        result: GraphExtractionResult, chunk_id: Optional[UUID]
    ) -> GraphExtractionResult:
        if not chunk_id:
            return result

        for node in result.nodes:
            properties = dict(getattr(node, "properties", {}) or {})
            properties["source_chunk_id"] = str(chunk_id)
            node.properties = properties
        return result

    def _dedupe_and_validate(self, extraction: ChunkGraphExtraction) -> ChunkGraphExtraction:
        entity_by_key: dict[str, Entity] = {}
        for entity in extraction.entities:
            key = entity.name.casefold().strip()
            if key not in entity_by_key:
                entity_by_key[key] = entity

        valid_entities = list(entity_by_key.values())
        valid_entity_keys = {entity.name.casefold().strip() for entity in valid_entities}

        seen_relations: set[tuple[str, str, str]] = set()
        valid_relations: list[Relation] = []

        for relation in extraction.relations:
            source_key = relation.source.casefold().strip()
            target_key = relation.target.casefold().strip()

            if not source_key or not target_key:
                continue
            if source_key == target_key:
                continue

            if source_key not in valid_entity_keys or target_key not in valid_entity_keys:
                continue

            relation_type = self._normalize_relation_type(relation.relation_type)
            relation_key = (source_key, target_key, relation_type)
            if relation_key in seen_relations:
                continue

            seen_relations.add(relation_key)
            valid_relations.append(
                Relation(
                    source=relation.source,
                    target=relation.target,
                    relation_type=relation_type,
                    description=relation.description,
                    weight=max(1, min(10, int(relation.weight))),
                )
            )

        return ChunkGraphExtraction(entities=valid_entities, relations=valid_relations)

    def _to_legacy_graph_result(
        self,
        extraction: ChunkGraphExtraction,
        chunk_id: Optional[UUID] = None,
    ) -> GraphExtractionResult:
        if extraction.is_empty():
            return self._empty_result()

        temp_by_entity_name: dict[str, str] = {}
        nodes: list[ExtractedNode] = []
        edges: list[ExtractedEdge] = []

        for idx, entity in enumerate(extraction.entities, start=1):
            temp_id = f"node_{idx}"
            temp_by_entity_name[entity.name.casefold().strip()] = temp_id
            nodes.append(
                ExtractedNode(
                    temp_id=temp_id,
                    name=entity.name,
                    node_type=entity.type,
                    content=entity.description,
                    properties={
                        "entity_type": entity.type,
                    },
                )
            )

        for relation in extraction.relations:
            source_temp = temp_by_entity_name.get(relation.source.casefold().strip())
            target_temp = temp_by_entity_name.get(relation.target.casefold().strip())

            if not source_temp or not target_temp:
                continue

            edges.append(
                ExtractedEdge(
                    source_temp_id=source_temp,
                    target_temp_id=target_temp,
                    edge_type=relation.relation_type,
                    description=relation.description,
                    weight=round(relation.weight / 10.0, 3),
                )
            )

        return self._attach_chunk_grounding(
            GraphExtractionResult(nodes=nodes, edges=edges), chunk_id
        )

    def _extract_with_json_fallback(self, text: str) -> ChunkGraphExtraction:
        if not self._llm:
            raise ValueError("LLM fallback unavailable: no BaseChatModel provided")

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._SYSTEM_PROMPT),
                ("human", "{prompt}\n\n{format_instructions}"),
            ]
        )
        chain = prompt | self._llm | self._json_fallback_parser
        return chain.invoke(
            {
                "prompt": self._USER_PROMPT.format(text=text),
                "format_instructions": self._json_fallback_parser.get_format_instructions(),
            }
        )

    async def _aextract_with_json_fallback(self, text: str) -> ChunkGraphExtraction:
        if not self._llm:
            raise ValueError("LLM fallback unavailable: no BaseChatModel provided")

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._SYSTEM_PROMPT),
                ("human", "{prompt}\n\n{format_instructions}"),
            ]
        )
        chain = prompt | self._llm | self._json_fallback_parser
        return await chain.ainvoke(
            {
                "prompt": self._USER_PROMPT.format(text=text),
                "format_instructions": self._json_fallback_parser.get_format_instructions(),
            }
        )

    async def _aextract_batch_with_json_fallback(
        self, batch_text: str
    ) -> BatchChunkGraphExtraction:
        if not self._llm:
            raise ValueError("LLM fallback unavailable: no BaseChatModel provided")

        batch_parser = PydanticOutputParser(pydantic_object=BatchChunkGraphExtraction)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self._SYSTEM_PROMPT),
                ("human", "{prompt}\n\n{format_instructions}"),
            ]
        )
        chain = prompt | self._llm | batch_parser
        return await chain.ainvoke(
            {
                "prompt": self._USER_BATCH_PROMPT.format(batch_text=batch_text),
                "format_instructions": batch_parser.get_format_instructions(),
            }
        )

    @staticmethod
    def _build_batch_text(texts: list[str]) -> str:
        sections: list[str] = []
        for idx, text in enumerate(texts, start=1):
            chunk_text = text.strip()
            sections.append(f"[CHUNK {idx}]\\n{chunk_text}")
        return "\n\n".join(sections)

    @staticmethod
    def _estimate_tokens_from_chars(char_len: int) -> int:
        return max(1, (int(char_len) + 3) // 4)

    def _should_force_per_chunk(self, texts: list[str]) -> bool:
        if len(texts) <= 1:
            return False

        max_batch_chars = max(
            2000,
            int(getattr(settings, "GRAPH_EXTRACTION_BATCH_MAX_CHARS", 24000) or 24000),
        )
        max_batch_estimated_tokens = max(
            500,
            int(getattr(settings, "GRAPH_EXTRACTION_BATCH_MAX_ESTIMATED_TOKENS", 6000) or 6000),
        )
        max_single_chunk_chars = max(
            1000,
            int(getattr(settings, "GRAPH_EXTRACTION_SINGLE_CHUNK_MAX_CHARS", 10000) or 10000),
        )

        total_chars = sum(len(str(text or "")) for text in texts)
        estimated_tokens = self._estimate_tokens_from_chars(total_chars)
        oversize_chunk = any(len(str(text or "")) > max_single_chunk_chars for text in texts)
        exceeds_batch_chars = total_chars > max_batch_chars
        exceeds_batch_tokens = estimated_tokens > max_batch_estimated_tokens

        if oversize_chunk or exceeds_batch_chars or exceeds_batch_tokens:
            logger.info(
                "graphrag_batch_guardrail_forced_per_chunk",
                chunks=len(texts),
                total_chars=total_chars,
                estimated_tokens=estimated_tokens,
                max_batch_chars=max_batch_chars,
                max_batch_estimated_tokens=max_batch_estimated_tokens,
                max_single_chunk_chars=max_single_chunk_chars,
                oversize_chunk=oversize_chunk,
                exceeds_batch_chars=exceeds_batch_chars,
                exceeds_batch_tokens=exceeds_batch_tokens,
            )
            return True
        return False

    async def extract_graph_batch_async(self, texts: list[str]) -> list[ChunkGraphExtraction]:
        if not texts:
            return []

        cleaned_texts = [text.strip() if isinstance(text, str) else "" for text in texts]
        if len(cleaned_texts) == 1:
            return [await self.extract_graph_from_chunk_async(cleaned_texts[0])]
        if self._should_force_per_chunk(cleaned_texts):
            return await asyncio.gather(
                *(self.extract_graph_from_chunk_async(text) for text in cleaned_texts)
            )

        batch_text = self._build_batch_text(cleaned_texts)
        if not batch_text.strip():
            return [ChunkGraphExtraction() for _ in cleaned_texts]

        try:
            extraction = await self._strict_engine.agenerate(
                prompt=self._USER_BATCH_PROMPT.format(batch_text=batch_text),
                schema=BatchChunkGraphExtraction,
                system_prompt=self._SYSTEM_PROMPT,
            )
        except Exception as primary_err:
            logger.warning(
                "Batch graph extraction failed via instructor; trying JSON fallback: %s",
                compact_error(primary_err),
            )
            try:
                extraction = await self._aextract_batch_with_json_fallback(batch_text=batch_text)
            except Exception as fallback_err:
                logger.warning(
                    "Batch graph extraction fallback failed; reverting to per-chunk calls: %s",
                    compact_error(fallback_err),
                )
                return await asyncio.gather(
                    *(self.extract_graph_from_chunk_async(text) for text in cleaned_texts)
                )

        by_index: dict[int, ChunkGraphExtraction] = {}
        for chunk_output in extraction.chunks:
            index = int(chunk_output.chunk_index)
            if index < 1 or index > len(cleaned_texts):
                continue
            normalized = self._dedupe_and_validate(
                ChunkGraphExtraction(
                    entities=chunk_output.entities,
                    relations=chunk_output.relations,
                )
            )
            by_index[index - 1] = normalized

        results: list[ChunkGraphExtraction] = []
        for idx in range(len(cleaned_texts)):
            results.append(by_index.get(idx, ChunkGraphExtraction()))
        return results

    def extract_graph_from_chunk(self, text: str) -> ChunkGraphExtraction:
        if not text or not text.strip():
            return ChunkGraphExtraction()

        prompt = self._USER_PROMPT.format(text=text.strip())

        try:
            extraction = self._strict_engine.generate(
                prompt=prompt,
                schema=ChunkGraphExtraction,
                system_prompt=self._SYSTEM_PROMPT,
            )
            return self._dedupe_and_validate(extraction)
        except Exception as primary_err:
            logger.warning(
                "Graph triple extraction failed via instructor; trying JSON fallback: %s",
                compact_error(primary_err),
            )
            try:
                extraction = self._extract_with_json_fallback(text)
                return self._dedupe_and_validate(extraction)
            except Exception as fallback_err:
                logger.error("Graph triple extraction failed: %s", fallback_err)
                return ChunkGraphExtraction()

    async def extract_graph_from_chunk_async(self, text: str) -> ChunkGraphExtraction:
        if not text or not text.strip():
            return ChunkGraphExtraction()

        prompt = self._USER_PROMPT.format(text=text.strip())

        try:
            extraction = await self._strict_engine.agenerate(
                prompt=prompt,
                schema=ChunkGraphExtraction,
                system_prompt=self._SYSTEM_PROMPT,
            )
            return self._dedupe_and_validate(extraction)
        except Exception as primary_err:
            logger.warning(
                "Async graph triple extraction failed via instructor; trying JSON fallback: %s",
                primary_err,
            )
            try:
                extraction = await self._aextract_with_json_fallback(text)
                return self._dedupe_and_validate(extraction)
            except Exception as fallback_err:
                logger.error("Async graph triple extraction failed: %s", fallback_err)
                return ChunkGraphExtraction()

    def extract(self, text: str, chunk_id: Optional[UUID] = None) -> GraphExtractionResult:
        extraction = self.extract_graph_from_chunk(text)
        result = self._to_legacy_graph_result(extraction, chunk_id=chunk_id)
        logger.info(
            "GraphExtractor: extracted %d entities and %d relations",
            len(extraction.entities),
            len(extraction.relations),
        )
        return result

    async def extract_async(
        self, text: str, chunk_id: Optional[UUID] = None
    ) -> GraphExtractionResult:
        extraction = await self.extract_graph_from_chunk_async(text)
        result = self._to_legacy_graph_result(extraction, chunk_id=chunk_id)
        logger.info(
            "GraphExtractor (async): extracted %d entities and %d relations",
            len(extraction.entities),
            len(extraction.relations),
        )
        return result
