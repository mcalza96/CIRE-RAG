"""LLM semantic triple extractor for dense knowledge graphs."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

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

    def __init__(
        self,
        llm: Optional[object] = None,
        strict_engine: Optional[StrictEngine] = None,
    ):
        self._unused_llm = llm
        self._strict_engine = strict_engine or get_strict_engine()
        self._max_concurrency = max(
            1, int(getattr(settings, "GRAPH_EXTRACTION_MAX_CONCURRENCY", 6) or 6)
        )
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._retry_max_attempts = max(
            1, int(getattr(settings, "GRAPH_EXTRACTION_RETRY_MAX_ATTEMPTS", 3) or 3)
        )
        self._retry_base_delay = max(
            0.05,
            float(getattr(settings, "GRAPH_EXTRACTION_RETRY_BASE_DELAY_SECONDS", 0.8) or 0.8),
        )
        self._retry_max_delay = max(
            self._retry_base_delay,
            float(getattr(settings, "GRAPH_EXTRACTION_RETRY_MAX_DELAY_SECONDS", 8.0) or 8.0),
        )
        self._retry_jitter = max(
            0.0,
            float(getattr(settings, "GRAPH_EXTRACTION_RETRY_JITTER_SECONDS", 0.35) or 0.35),
        )

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

    def _is_retryable_error(self, err: Exception) -> bool:
        text = str(err or "").lower()
        return any(
            marker in text
            for marker in (
                "429",
                "rate limit",
                "timeout",
                "timed out",
                "connection",
                "temporarily unavailable",
                "503",
                "504",
                "502",
            )
        )

    def _retry_delay_seconds(self, attempt: int) -> float:
        exponential = self._retry_base_delay * (2 ** max(0, attempt - 1))
        bounded = min(self._retry_max_delay, exponential)
        jitter = random.uniform(0.0, self._retry_jitter) if self._retry_jitter else 0.0
        return bounded + jitter

    async def _extract_graph_with_retry_async(self, text: str) -> ChunkGraphExtraction:
        prompt = self._USER_PROMPT.format(text=text.strip())
        last_error: Optional[Exception] = None

        for attempt in range(1, self._retry_max_attempts + 1):
            try:
                extraction = await self._strict_engine.agenerate(
                    prompt=prompt,
                    schema=ChunkGraphExtraction,
                    system_prompt=self._SYSTEM_PROMPT,
                )
                return self._dedupe_and_validate(extraction)
            except Exception as err:
                last_error = err
                should_retry = attempt < self._retry_max_attempts and self._is_retryable_error(err)
                if not should_retry:
                    break

                delay = self._retry_delay_seconds(attempt)
                logger.warning(
                    "graph_extraction_retry attempt=%s delay=%.2f error=%s",
                    attempt,
                    delay,
                    compact_error(err),
                )
                await asyncio.sleep(delay)

        logger.error("graph_extraction_failed error=%s", compact_error(last_error or Exception("")))
        return ChunkGraphExtraction()

    async def extract_graph_batch_async(self, texts: list[str]) -> list[ChunkGraphExtraction]:
        if not texts:
            return []

        cleaned_texts = [text.strip() if isinstance(text, str) else "" for text in texts]

        async def _run_one(text: str) -> ChunkGraphExtraction:
            if not text:
                return ChunkGraphExtraction()
            async with self._semaphore:
                return await self._extract_graph_with_retry_async(text)

        tasks = [asyncio.create_task(_run_one(text)) for text in cleaned_texts]
        return await asyncio.gather(*tasks)

    def extract_graph_from_chunk(self, text: str) -> ChunkGraphExtraction:
        if not text or not text.strip():
            return ChunkGraphExtraction()

        prompt = self._USER_PROMPT.format(text=text.strip())
        last_error: Optional[Exception] = None
        for attempt in range(1, self._retry_max_attempts + 1):
            try:
                extraction = self._strict_engine.generate(
                    prompt=prompt,
                    schema=ChunkGraphExtraction,
                    system_prompt=self._SYSTEM_PROMPT,
                )
                return self._dedupe_and_validate(extraction)
            except Exception as err:
                last_error = err
                should_retry = attempt < self._retry_max_attempts and self._is_retryable_error(err)
                if not should_retry:
                    break
                delay = self._retry_delay_seconds(attempt)
                logger.warning(
                    "graph_extraction_retry_sync attempt=%s delay=%.2f error=%s",
                    attempt,
                    delay,
                    compact_error(err),
                )
                time.sleep(delay)

        logger.error(
            "graph_extraction_sync_failed error=%s", compact_error(last_error or Exception(""))
        )
        return ChunkGraphExtraction()

    async def extract_graph_from_chunk_async(self, text: str) -> ChunkGraphExtraction:
        if not text or not text.strip():
            return ChunkGraphExtraction()
        async with self._semaphore:
            return await self._extract_graph_with_retry_async(text)

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
