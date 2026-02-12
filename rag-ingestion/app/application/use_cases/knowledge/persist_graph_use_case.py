import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.graph_extractor import ChunkGraphExtraction

logger = logging.getLogger(__name__)

class PersistGraphUseCase:
    """
    Use Case for persisting extracted regulatory knowledge graphs.
    
    Orchestrates:
    1. Deterministic ID generation.
    2. Vector embedding generation (optional).
    3. Repository persistence (Atomic batching handled by repository).
    """

    def __init__(
        self,
        repository,
        embedding_service: Optional[JinaEmbeddingService] = None,
        generate_embeddings: bool = True,
    ):
        self.repository = repository
        self.generate_embeddings = generate_embeddings
        self.embedding_service = embedding_service or (
            JinaEmbeddingService.get_instance() if generate_embeddings else None
        )

    @dataclass
    class GraphPersistenceResult:
        nodes_upserted: int = 0
        edges_inserted: int = 0
        links_upserted: int = 0
        errors: list[str] = field(default_factory=list)

    async def execute(
        self,
        extraction: ChunkGraphExtraction,
        tenant_id: str,
        chunk_id: Optional[str] = None,
    ) -> "PersistGraphUseCase.GraphPersistenceResult":
        result = self.GraphPersistenceResult()
        
        if extraction.is_empty():
            logger.info("Empty extraction, nothing to persist")
            return result
        
        tenant_uuid = UUID(str(tenant_id))
        chunk_uuid = UUID(str(chunk_id)) if chunk_id else None

        entity_embeddings: dict[str, list[float]] = {}
        if self.generate_embeddings and self.embedding_service and extraction.entities:
            try:
                embedding_texts = [f"{entity.name}. {entity.description}" for entity in extraction.entities]
                embeddings = await self.embedding_service.embed_texts(embedding_texts)
                for entity, embedding in zip(extraction.entities, embeddings):
                    if embedding:
                        entity_embeddings[entity.name.strip().casefold()] = embedding
            except Exception as e:
                logger.warning(f"Failed to generate entity embeddings: {e}")
                result.errors.append(f"Embedding generation failed: {e}")

        try:
            stats = await self.repository.upsert_knowledge_subgraph(
                extraction=extraction,
                chunk_id=chunk_uuid,
                tenant_id=tenant_uuid,
                entity_embeddings=entity_embeddings,
            )
            result.nodes_upserted = stats.get("nodes_upserted", 0)
            result.edges_inserted = stats.get("edges_upserted", 0)
            result.links_upserted = stats.get("links_upserted", 0)
            if stats.get("errors"):
                result.errors.extend(stats["errors"])
        except Exception as e:
            logger.error("PersistGraphUseCase failed to persist knowledge subgraph: %s", e)
            result.errors.append(str(e))

        logger.info(
            "PersistGraphUseCase: Persisted %s entities, %s relations, %s provenance links",
            result.nodes_upserted,
            result.edges_inserted,
            result.links_upserted,
        )

        return result
