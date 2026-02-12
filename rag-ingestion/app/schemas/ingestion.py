from pydantic import BaseModel, ConfigDict, Field, field_validator
from uuid import UUID
from typing import List, Optional
from enum import Enum
import re
import html

from app.domain.types.authority import AuthorityLevel

class IngestionType(str, Enum):
    CONTENT = "content"
    RUBRIC = "rubric"

class IngestionMetadata(BaseModel):
    """
    Metadata payload for the ingestion endpoint.
    Mirrors the frontend SmartUploadSchema but with optional fields for resilience.
    """
    title: str = Field(..., min_length=1, description="Human friendly title for the document")
    # All taxonomy fields are optional to handle varied metadata formats (Global Content vs Course Content)
    type_id: Optional[UUID] = Field(None, alias="typeId", description="Root Node ID (Content or Rubric)")
    context_id: Optional[UUID] = Field(None, alias="contextId", description="Context Node ID (Regular, TP, Adults...)")
    level_ids: Optional[List[UUID]] = Field(default_factory=list, alias="levelIds", description="Level Node IDs (7b, 8b...)")
    subject_id: Optional[UUID] = Field(None, alias="subjectId", description="Subject Node ID (Matemática, Lenguaje...)")
    
    # Optional fields that might be useful for the strategy
    doc_type: Optional[str] = Field(None, description="Detailed document type (e.g. 'texto_estudiante')")
    document_type_id: Optional[UUID] = Field(None, alias="documentTypeId", description="Taxonomy ID for document type")

    # Internal tracking fields (populated by worker/dispatcher)
    source_id: Optional[UUID] = Field(None, description="ID of the source document in DB")
    institution_id: Optional[UUID] = Field(None, description="ID of the institution")
    is_global: bool = Field(False, description="Whether this content is global")
    global_summary: Optional[str] = Field(None, description="Contextual summary for retrieval augmentation")
    storage_path: Optional[str] = Field(None, description="Internal storage path (local or S3 key)")
    
    # Authority taxonomy for semantic reranking (Phase 1 - Context Engine)
    authority_level: AuthorityLevel = Field(
        default=AuthorityLevel.SUPPLEMENTARY, 
        description="Semantic authority level for deterministic reranking"
    )

    enforcement_level: Optional[str] = Field(
        None, 
        alias="enforcementLevel",
        description="Enforcement level from institutional policy ('advisory' or 'hard_constraint')"
    )
    
    # Catch-all for extra metadata (like embedding_mode)
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata payload")

    @field_validator("title", "global_summary")
    @classmethod
    def sanitize_metadata_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # 1. Strip HTML tags
        v = re.sub(r'<[^>]*>', '', v)
        # 2. Escape HTML entities
        v = html.escape(v)
        # 3. Limit length to prevent buffer/DOS issues
        return v[:2000].strip()

    model_config = ConfigDict(populate_by_name=True)
