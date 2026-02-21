"""
RAPTOR Processor - Recursive Abstractive Processing for Tree-Organized Retrieval.

Implements hierarchical summarization of base chunks using:
1. GMM Clustering (Scikit-learn)
2. LLM Summarization (via get_llm factory)
3. Recursive tree building
"""

import logging
import numpy as np
import asyncio
from typing import Any, List, Optional, Dict
from uuid import UUID, uuid4

from app.domain.schemas.raptor_schemas import BaseChunk, SummaryNode, RaptorTreeResult
from app.domain.repositories.raptor_repository import IRaptorRepository
from app.services.knowledge.clustering_service import GMMClusteringService
from app.services.knowledge.summarization_service import SummarizationAgent
from app.ai.embeddings import JinaEmbeddingService
from app.infrastructure.settings import settings

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
        self._summarization_semaphore = asyncio.Semaphore(
            max(1, int(getattr(settings, "RAPTOR_SUMMARIZATION_MAX_CONCURRENCY", 8) or 8))
        )

    async def _asummarize_cluster(self, cluster_texts: List[str]) -> tuple[str, str]:
        async with self._summarization_semaphore:
            return await self.summarizer.asummarize(cluster_texts)

    async def _build_summary_nodes_for_level(
        self,
        *,
        level_items: List[Dict[str, Any]],
        current_level: int,
        tenant_id: UUID,
        source_document_id: Optional[UUID],
        collection_id: Optional[UUID],
        level_source_standard: Optional[str],
        embedding_mode: Optional[str],
    ) -> List[BaseChunk]:
        if not level_items:
            return []

        summarize_tasks = [
            asyncio.create_task(self._asummarize_cluster(item["cluster_texts"]))
            for item in level_items
        ]
        summarize_results = await asyncio.gather(*summarize_tasks, return_exceptions=True)

        valid_items: List[tuple[Dict[str, Any], str, str]] = []
        for item, summary_result in zip(level_items, summarize_results):
            if isinstance(summary_result, BaseException):
                logger.warning(
                    "raptor_cluster_summarization_failed level=%s cluster=%s error=%s",
                    current_level,
                    item.get("cluster_id"),
                    str(summary_result),
                )
                continue
            if not isinstance(summary_result, tuple) or len(summary_result) != 2:
                logger.warning(
                    "raptor_cluster_summarization_invalid_result level=%s cluster=%s",
                    current_level,
                    item.get("cluster_id"),
                )
                continue
            title, summary = summary_result
            valid_items.append((item, title, summary))

        if not valid_items:
            return []

        summary_texts = [summary for _, _, summary in valid_items]
        embeddings = await self.embedding_service.embed_texts(summary_texts, mode=embedding_mode)

        summary_nodes: List[SummaryNode] = []
        next_level_nodes: List[BaseChunk] = []
        for (item, title, summary), summary_embedding in zip(valid_items, embeddings):
            summary_node_id = uuid4()
            summary_node = SummaryNode(
                id=summary_node_id,
                content=summary,
                title=title,
                embedding=summary_embedding,
                level=current_level,
                children_ids=item["children_ids"],
                children_summary_ids=item.get("children_summary_ids", []),
                tenant_id=tenant_id,
                source_document_id=source_document_id,
                collection_id=collection_id,
                source_standard=level_source_standard,
                section_ref=item.get("section_ref"),
                section_node_id=item.get("section_node_id"),
            )
            summary_nodes.append(summary_node)
            next_level_nodes.append(
                BaseChunk(
                    id=summary_node_id,
                    content=summary_node.content,
                    embedding=list(summary_embedding),
                    tenant_id=tenant_id,
                    source_standard=level_source_standard,
                    section_ref=item.get("section_ref"),
                    section_node_id=item.get("section_node_id"),
                    is_summary_node=True,
                )
            )

        await self.repository.save_summary_nodes(summary_nodes)
        return next_level_nodes

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
        level_items: List[Dict[str, Any]] = []

        for section_ref, section_nodes in groups.items():
            cluster_texts = [
                str(getattr(item, "content", "") or "").strip() for item in section_nodes
            ]
            cluster_texts = [text for text in cluster_texts if text]
            if not cluster_texts:
                continue

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
            level_items.append(
                {
                    "cluster_id": section_ref,
                    "cluster_texts": cluster_texts,
                    "children_ids": children_ids,
                    "children_summary_ids": children_summary_ids,
                    "section_ref": section_ref,
                    "section_node_id": section_node_id,
                }
            )

        return await self._build_summary_nodes_for_level(
            level_items=level_items,
            current_level=current_level,
            tenant_id=tenant_id,
            source_document_id=source_document_id,
            collection_id=collection_id,
            level_source_standard=level_source_standard,
            embedding_mode=embedding_mode,
        )

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
                        "raptor_structural_level_created level=%s nodes=%s",
                        current_level,
                        len(structural_nodes),
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

            level_items: List[Dict[str, Any]] = []

            for cluster_id, cluster_chunk_ids in cluster_result.cluster_contents.items():
                # Get text content for this cluster
                cluster_texts = [content_lookup.get(str(cid), "") for cid in cluster_chunk_ids]
                cluster_texts = [t for t in cluster_texts if t]  # Filter out missing

                if not cluster_texts:
                    logger.warning(f"Cluster {cluster_id} has no valid text content, skipping")
                    continue

                level_items.append(
                    {
                        "cluster_id": cluster_id,
                        "cluster_texts": cluster_texts,
                        "children_ids": cluster_chunk_ids,
                        "children_summary_ids": [
                            UUID(str(cid))
                            for cid in cluster_chunk_ids
                            if bool(getattr(node_lookup.get(str(cid)), "is_summary_node", False))
                        ],
                    }
                )

            new_level_nodes = await self._build_summary_nodes_for_level(
                level_items=level_items,
                current_level=current_level,
                tenant_id=tenant_id,
                source_document_id=source_document_id,
                collection_id=collection_id,
                level_source_standard=level_source_standard,
                embedding_mode=embedding_mode,
            )
            total_created += len(new_level_nodes)

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
