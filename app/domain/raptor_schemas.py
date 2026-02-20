"""
RAPTOR Schemas - Pydantic models for hierarchical summarization.

Defines data structures for the RAPTOR (Recursive Abstractive Processing
for Tree-Organized Retrieval) system.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID


class BaseChunk(BaseModel):
    """A base-level chunk from JinaLateChunker (Level 0)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID
    content: str
    embedding: List[float]
    tenant_id: UUID
    source_standard: Optional[str] = None
    section_ref: Optional[str] = None
    section_node_id: Optional[UUID] = None
    is_summary_node: bool = False


class ClusterAssignment(BaseModel):
    """
    Result of soft GMM clustering.
    A chunk can belong to multiple clusters with different probabilities.
    """

    chunk_id: UUID
    cluster_id: int
    probability: float = Field(ge=0.0, le=1.0)


class ClusterResult(BaseModel):
    """Result of clustering a set of chunks."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    num_clusters: int
    assignments: List[ClusterAssignment]
    # Chunks grouped by cluster_id
    cluster_contents: Dict[int, List[UUID]]  # {cluster_id: [chunk_ids]}


class SummaryNode(BaseModel):
    """A summary node generated from a cluster of chunks."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: Optional[UUID] = None  # Assigned after persistence
    content: str
    title: str
    embedding: Optional[List[float]] = None
    level: int
    children_ids: List[UUID]
    tenant_id: UUID
    source_document_id: Optional[UUID] = None
    collection_id: Optional[UUID] = None
    source_standard: Optional[str] = None
    section_ref: Optional[str] = None
    section_node_id: Optional[UUID] = None
    children_summary_ids: List[UUID] = Field(default_factory=list)


class RaptorTreeResult(BaseModel):
    """Result of building a complete RAPTOR tree."""

    root_node_id: UUID
    total_nodes_created: int
    max_depth: int
    levels: Dict[int, List[UUID]]  # {level: [node_ids]}
