from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime

class SourceStatus(str, Enum):
    QUEUED = 'queued'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'

class Chunk(BaseModel):
    id: UUID
    content: str
    page_number: int = Field(..., alias="pageNumber")
    token_density: float = Field(..., alias="tokenDensity")
    semantic_context: Optional[str] = Field(None, alias="semanticContext")
    source_id: UUID = Field(..., alias="sourceId")

    model_config = ConfigDict(populate_by_name=True)

class SourceMetadata(BaseModel):
    title: Optional[str] = None
    type_id: Optional[UUID] = Field(None, alias="typeId")
    context_id: Optional[UUID] = Field(None, alias="contextId")
    level_ids: List[UUID] = Field(default_factory=list, alias="levelIds")
    subject_id: Optional[UUID] = Field(None, alias="subjectId")
    doc_type: Optional[str] = None
    document_type_id: Optional[UUID] = Field(None, alias="documentTypeId")
    source_id: Optional[UUID] = None
    institution_id: Optional[UUID] = None
    is_global: bool = False
    global_summary: Optional[str] = None
    authority_level: Optional[str] = "soft_knowledge"

    model_config = ConfigDict(populate_by_name=True, extra="allow")

class Source(BaseModel):
    id: UUID
    filename: str
    file_size: Optional[int] = Field(None, alias="fileSize")
    storage_path: Optional[str] = Field(None, alias="storagePath")
    content_type: Optional[str] = Field(None, alias="contentType")
    status: SourceStatus
    chunks_count: int = Field(0, alias="chunksCount")
    uploaded_at: Optional[datetime] = Field(None, alias="uploadedAt")
    course_id: UUID = Field(..., alias="courseId")
    chunks: List[Chunk] = Field(default_factory=list)
    origin: str = 'uploaded'
    is_global: bool = Field(False, alias="isGlobal")
    institution_id: Optional[UUID] = Field(None, alias="institutionId")
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)

    model_config = ConfigDict(populate_by_name=True)
