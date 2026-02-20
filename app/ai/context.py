"""Unified retrieval context models for hydrated RAG responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextItem(BaseModel):
    """Normalized context payload consumed by downstream agents/LLMs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source_type: str = Field(description="document_chunk or visual_node")
    content: str
    similarity: float
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_visual_anchor: bool = False
    source_id: str | None = None
    parent_chunk_id: str | None = None


class UnifiedSearchRow(BaseModel):
    """Typed shape returned by the unified SQL search function."""

    model_config = ConfigDict(extra="ignore")

    id: str
    source_type: str
    similarity: float
    score: float
    content: str | None = None
    visual_summary: str | None = None
    structured_reconstruction: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_id: str | None = None
    parent_chunk_id: str | None = None
