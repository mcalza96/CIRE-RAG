"""
RAPTOR Processor - Recursive Abstractive Processing for Tree-Organized Retrieval.

Implements hierarchical summarization of base chunks using:
1. GMM Clustering (Scikit-learn)
2. LLM Summarization (via get_llm factory)
3. Recursive tree building
"""

import logging
import numpy as np
from typing import List, Optional, Dict
from uuid import UUID, uuid4

from app.domain.raptor_schemas import BaseChunk, ClusterResult, SummaryNode, RaptorTreeResult
from app.domain.repositories.raptor_repository import IRaptorRepository
from app.services.knowledge.clustering_service import GMMClusteringService
from app.services.knowledge.summarization_service import SummarizationAgent
from app.services.embedding_service import JinaEmbeddingService
from app.core.settings import settings

logger = logging.getLogger(__name__)


# Prompts moved to app.core.prompt_registry.PromptRegistry


# =============================================================================
# RAPTOR PROCESSOR (Main Orchestrator)
# =============================================================================


class RaptorProcessor:
    """
    Orchestrates the RAPTOR tree building process.

    Workflow:
    1. Receive base chunks (Level 0)
    2. Cluster -> Summarize -> Create Level 1 nodes
    3. Recurse until single cluster or max depth
    """

    def __init__(
        self,
        repository: IRaptorRepository,
        embedding_service: Optional[JinaEmbeddingService] = None,
        clustering_service: Optional[GMMClusteringService] = None,
        summarization_service: Optional[SummarizationAgent] = None,
        max_depth: int = 3,
    ):
        self.repository = repository
        self.embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self.clustering = clustering_service or GMMClusteringService()
        self.summarizer = summarization_service or SummarizationAgent()
        self.max_depth = max_depth

    @staticmethod
    def _group_nodes_by_structure(
        current_level_nodes: List[BaseChunk],
    ) -> dict[str, List[BaseChunk]]:
        groups: dict[str, List[BaseChunk]] = {}
        for node in current_level_nodes:
            section_ref = str(getattr(node, "section_ref", "") or "").strip()
            if not section_ref:
                continue
            groups.setdefault(section_ref, []).append(node)
        return groups

    async def _build_structural_level(
        self,
        *,
        current_level_nodes: List[BaseChunk],
        current_level: int,
        tenant_id: UUID,
        source_document_id: Optional[UUID],
        collection_id: Optional[UUID],
        embedding_mode: Optional[str],
    ) -> List[BaseChunk]:
        groups = self._group_nodes_by_structure(current_level_nodes)
        if not groups:
            return []

        level_source_standard = getattr(current_level_nodes[0], "source_standard", None)
        new_level_nodes: List[BaseChunk] = []

        for section_ref, section_nodes in groups.items():
            cluster_texts = [
                str(getattr(item, "content", "") or "").strip() for item in section_nodes
            ]
            cluster_texts = [text for text in cluster_texts if text]
            if not cluster_texts:
                continue

            title, summary = self.summarizer.summarize(cluster_texts)
            vectors = await self.embedding_service.embed_texts([summary], mode=embedding_mode)
            summary_embedding = vectors[0]

            children_ids = [item.id for item in section_nodes]
            children_summary_ids = [item.id for item in section_nodes if bool(item.is_summary_node)]
            section_node_id = next(
                (
                    item.section_node_id
                    for item in section_nodes
                    if getattr(item, "section_node_id", None) is not None
                ),
                None,
            )

            summary_node = SummaryNode(
                id=uuid4(),
                content=summary,
                title=title,
                embedding=summary_embedding,
                level=current_level,
                children_ids=children_ids,
                children_summary_ids=children_summary_ids,
                tenant_id=tenant_id,
                source_document_id=source_document_id,
                collection_id=collection_id,
                source_standard=level_source_standard,
                section_ref=section_ref,
                section_node_id=section_node_id,
            )
            await self.repository.save_summary_node(summary_node)

            new_level_nodes.append(
                BaseChunk(
                    id=summary_node.id,
                    content=summary_node.content,
                    embedding=summary_node.embedding or [],
                    tenant_id=tenant_id,
                    source_standard=level_source_standard,
                    section_ref=section_ref,
                    section_node_id=section_node_id,
                    is_summary_node=True,
                )
            )

        return new_level_nodes

    async def build_tree(
        self,
        base_chunks: List[BaseChunk],
        tenant_id: UUID,
        source_document_id: Optional[UUID] = None,
        collection_id: Optional[UUID] = None,
        embedding_mode: Optional[str] = None,
    ) -> RaptorTreeResult:
        """
        Build a complete RAPTOR tree from base chunks.

        Args:
            base_chunks: List of Level 0 chunks with embeddings.
            tenant_id: Tenant ID for multi-tenant isolation.
            source_document_id: Optional link to source document.

        Returns:
            RaptorTreeResult with tree structure information.
        """
        logger.info(f"Building RAPTOR tree for {len(base_chunks)} base chunks")

        if not base_chunks:
            logger.warning("No base chunks provided, skipping RAPTOR tree building")
            return RaptorTreeResult(
                root_node_id=uuid4(), total_nodes_created=0, max_depth=0, levels={}
            )

        levels: Dict[int, List[UUID]] = {0: [c.id for c in base_chunks]}
        current_level_nodes = base_chunks
        current_level = 0
        total_created = 0
        structural_mode_enabled = bool(getattr(settings, "RAPTOR_STRUCTURAL_MODE_ENABLED", True))
        structural_bootstrap_done = False

        while True:
            current_level += 1

            if current_level > self.max_depth:
                logger.info(f"Reached max depth ({self.max_depth}), stopping")
                break

            if len(current_level_nodes) <= 1:
                logger.info("Single node remaining, stopping")
                break

            if structural_mode_enabled and not structural_bootstrap_done:
                structural_nodes = await self._build_structural_level(
                    current_level_nodes=current_level_nodes,
                    current_level=current_level,
                    tenant_id=tenant_id,
                    source_document_id=source_document_id,
                    collection_id=collection_id,
                    embedding_mode=embedding_mode,
                )
                if structural_nodes:
                    structural_bootstrap_done = True
                    total_created += len(structural_nodes)
                    levels[current_level] = [n.id for n in structural_nodes]
                    current_level_nodes = structural_nodes
                    logger.info(
                        "raptor_structural_level_created",
                        level=current_level,
                        nodes=len(structural_nodes),
                    )
                    continue

            # Extract embeddings for clustering
            embeddings = np.array([c.embedding for c in current_level_nodes])
            chunk_ids = [c.id for c in current_level_nodes]

            # Cluster
            cluster_result = self.clustering.cluster(chunk_ids, embeddings)

            if cluster_result.num_clusters <= 1:
                logger.info("Converged to single cluster, stopping")
                break

            # Extract common metadata from first chunk in level to propagate (assuming same source)
            first_chunk = current_level_nodes[0]
            level_source_standard = getattr(first_chunk, "source_standard", None)

            # Create content lookup for summarization (use strings for robustness)
            content_lookup = {str(c.id): c.content for c in current_level_nodes}
            node_lookup = {str(c.id): c for c in current_level_nodes}

            # Summarize each cluster
            new_level_nodes: List[BaseChunk] = []

            for cluster_id, cluster_chunk_ids in cluster_result.cluster_contents.items():
                # Get text content for this cluster
                cluster_texts = [content_lookup.get(str(cid), "") for cid in cluster_chunk_ids]
                cluster_texts = [t for t in cluster_texts if t]  # Filter out missing

                if not cluster_texts:
                    logger.warning(f"Cluster {cluster_id} has no valid text content, skipping")
                    continue

                # Generate summary
                title, summary = self.summarizer.summarize(cluster_texts)

                # Generate embedding for summary
                embeddings = await self.embedding_service.embed_texts(
                    [summary],
                    mode=embedding_mode,
                )
                summary_embedding = embeddings[0]

                # Create summary node
                summary_node = SummaryNode(
                    id=uuid4(),
                    content=summary,
                    title=title,
                    embedding=summary_embedding,
                    level=current_level,
                    children_ids=cluster_chunk_ids,
                    tenant_id=tenant_id,
                    source_document_id=source_document_id,
                    collection_id=collection_id,
                    source_standard=level_source_standard,
                    children_summary_ids=[
                        UUID(str(cid))
                        for cid in cluster_chunk_ids
                        if bool(getattr(node_lookup.get(str(cid)), "is_summary_node", False))
                    ],
                )

                # Persist via repository
                await self.repository.save_summary_node(summary_node)
                total_created += 1

                # Add to next iteration as BaseChunk-like object
                # Update BaseChunk with source_standard if needed for further propagation
                new_level_chunk = BaseChunk(
                    id=summary_node.id,
                    content=summary_node.content,
                    embedding=summary_node.embedding,
                    tenant_id=tenant_id,
                    source_standard=level_source_standard,
                    is_summary_node=True,
                )
                new_level_nodes.append(new_level_chunk)

            levels[current_level] = [n.id for n in new_level_nodes]
            current_level_nodes = new_level_nodes

            logger.info(f"Level {current_level}: Created {len(new_level_nodes)} summary nodes")

        # The last remaining node is the root
        root_id = current_level_nodes[0].id if current_level_nodes else base_chunks[0].id

        return RaptorTreeResult(
            root_node_id=root_id,
            total_nodes_created=total_created,
            max_depth=current_level,
            levels=levels,
        )

    # _persist_summary_node moved to IRaptorRepository implementation
