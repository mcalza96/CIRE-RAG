from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from pydantic.alias_generators import to_camel

class AgentRole(str, Enum):
    ACADEMIC_AUDITOR = "academic_auditor"
    SOCRATIC_MENTOR = "socratic_mentor"
    CONTENT_DESIGNER = "content_designer"
    INTEGRITY_GUARD = "integrity_guard"

class TaskType(str, Enum):
    GRADING = "grading"
    IDEATION = "ideation"
    FACT_CHECKING = "fact_checking"
    EXPLANATION = "explanation"

class CamelModel(BaseModel):
    """Base model that automatically converts snake_case to camelCase for JSON."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        use_enum_values=True
    )

class RetrievalMode(str, Enum):
    SCOPED = 'scoped'
    PARENT_FALLBACK = 'parent_fallback'
    GLOBAL_FALLBACK = 'global_fallback'
    AUDIT = 'audit'

class AuthorityLevel(str, Enum):
    ADMINISTRATIVE = 'administrative'
    CONSTITUTION = 'constitution'
    POLICY = 'policy'
    CANONICAL = 'canonical'
    SUPPLEMENTARY = 'supplementary'
    HARD_CONSTRAINT = 'hard_constraint'
    SOFT_KNOWLEDGE = 'soft_knowledge'

class RetrievalFilters(CamelModel):
    min_authority_level: Optional[AuthorityLevel] = None
    subject_id: Optional[str] = None
    level_ids: Optional[List[str]] = None
    global_only: Optional[bool] = None
    institutional_only: Optional[bool] = None
    limit: Optional[int] = None
    min_similarity: Optional[float] = None
    allowed_global_ids: Optional[List[str]] = None
    source_id: Optional[str] = None
    collection_id: Optional[str] = None

class RAGSearchResult(CamelModel):
    score: float
    source_layer: str
    id: str
    content: str
    similarity: float
    metadata: Dict[str, Any]
    source_id: Optional[str] = None
    semantic_context: Optional[str] = None

class RetrievalIntent(CamelModel):
    metadata: Optional[Dict[str, Any]] = None
    query: str
    role: AgentRole
    task: TaskType
    tenant_id: Optional[str] = None
    course_id: Optional[str] = None
    user_id: Optional[str] = None
    filters: Optional[RetrievalFilters] = None

class Citation(CamelModel):
    source_id: str
    filename: str
    page_number: Optional[int] = None
    semantic_context: Optional[str] = None
    authority_level: Optional[AuthorityLevel] = None

class GroundedContext(CamelModel):
    text_block: str = Field(..., description="The formatted text block ready for injection")
    citations: List[Citation]
    chunks: List[RAGSearchResult]
    trace_id: Optional[str] = None
    retrieval_mode: Optional[RetrievalMode] = None
