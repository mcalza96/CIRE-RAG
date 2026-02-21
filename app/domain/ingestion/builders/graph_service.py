import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from uuid import UUID

from app.ai.embeddings import JinaEmbeddingService
from app.domain.ingestion.builders.graph_extractor import ChunkGraphExtraction

logger = logging.getLogger(__name__)

@dataclass
class GraphPersistenceResult:
    nodes_upserted: int = 0
    edges_inserted: int = 0
    links_upserted: int = 0
    errors: List[str] = field(default_factory=list)

class GraphGroundedRetrievalWorkflow:
    """
    Service for managing and persisting extracted regulatory knowledge graphs.
    Formerly PersistGraphUseCase.
    """

    def __init__(
        self,
        repository: Any,
        embedding_service: Optional[JinaEmbeddingService] = None,
        generate_embeddings: bool = True,
    ):
        self.repository = repository
        self.generate_embeddings = generate_embeddings
        self.embedding_service = embedding_service or (
            JinaEmbeddingService.get_instance() if generate_embeddings else None
        )

    async def persist_graph(
        self,
        extraction: ChunkGraphExtraction,
        tenant_id: str,
        chunk_id: Optional[str] = None,
    ) -> GraphPersistenceResult:
        """Orchestrates persistence of knowledge entities and relations."""
        result = GraphPersistenceResult()
        
        if extraction.is_empty():
            logger.info("Empty extraction, nothing to persist")
            return result
        
        tenant_uuid = UUID(str(tenant_id))
        chunk_uuid = UUID(str(chunk_id)) if chunk_id else None

        entity_embeddings: Dict[str, List[float]] = {}
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
            logger.error("GraphGroundedRetrievalWorkflow failed to persist knowledge subgraph: %s", e)
            result.errors.append(str(e))

        return result
