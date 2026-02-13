"""
State definition for the structured synthesis graph.
Tracks discovery and selection of content for synthesis generation.
"""
from typing import TypedDict, Optional, List, Dict, Any

class ConceptCandidate(TypedDict):
    chunk_id: str
    content: str
    relevance_score: float
    metadata: Dict[str, Any]

class SelectedConcept(TypedDict):
    title: str
    summary: str
    rationale: str
    linked_chunk_ids: List[str]

class CurriculumState(TypedDict):
    # Input
    topic: str
    course_level: str
    source_document_id: str
    tenant_id: str # For RLS/Isolation

    # Internal Processing
    retrieved_candidates: List[ConceptCandidate] # Output of Explorer
    
    # Output
    selected_concepts: List[SelectedConcept] # Output of Curator
    error: Optional[str]
