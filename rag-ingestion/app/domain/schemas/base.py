"""
Domain schemas for the RAG Ingestion service using Pydantic.
Ensures type safety and clear definition of data structures.
Follows CISRE v2.3 naming conventions:
- camelCase for domain/entities.
- snake_case for fields mapping to the database.
"""
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

class ContentChunk(BaseModel):
    """
    Domain entity for a piece of processed content.
    """
    id: Optional[UUID] = None
    sourceId: UUID = Field(alias="source_id")
    content: str
    semanticContext: str = Field(alias="semantic_context")
    embedding: List[float]
    filePageNumber: int = Field(alias="file_page_number")
    chunkIndex: int = Field(alias="chunk_index")
    metadata: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)

class SourceDocument(BaseModel):
    """
    Domain entity for the original source file.
    """
    id: Optional[UUID] = None
    courseId: UUID = Field(alias="course_id")
    filename: str
    metadata: Dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True)

class DocumentSegment(BaseModel):
    """
    Intermediate representation of a segmented document part.
    """
    title: str
    text: str
    startPage: int = Field(alias="start_page")
    endPage: int = Field(alias="end_page")
