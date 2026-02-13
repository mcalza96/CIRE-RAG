"""
State definition for the Institutional Ingest Graph.
Enforces strict tenant isolation and administrative document types.
"""
from typing import TypedDict, Optional, List, Literal, Dict, Any

class InstitutionalState(TypedDict):
    # Input
    tenant_id: str  # Critical for security isolation
    doc_type: Literal['regulatory', 'administrative']
    file_path: str
    document_id: str  # Added for traceability and linkage
    
    # Internal Processing
    raw_text: Optional[str]
    parsed_content: Optional[str] # Markdown with headers
    semantic_chunks: List[Dict[str, Any]] # [{'content': str, 'embedding': List[float], 'metadata': dict }]
    
    # Output
    status: str # 'success', 'failed'
    error: Optional[str]
    indexed_count: int
