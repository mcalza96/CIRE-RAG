"""
Authority Classification Service.

Infers AuthorityLevel based on file path patterns and document metadata.
Designed to be extensible - add new rules to CLASSIFICATION_RULES.
"""
import re
from typing import Optional, List, Tuple

from app.domain.types.authority import AuthorityLevel


class AuthorityClassifier:
    """
    Domain service that infers AuthorityLevel from storage paths and metadata.
    
    Uses a rule-based approach with pattern matching for extensibility.
    Default fallback is SUPPLEMENTARY (lowest priority).
    """
    
    # Rules ordered by priority (first match wins)
    # Format: (patterns: List[str], authority: AuthorityLevel)
    CLASSIFICATION_RULES: List[Tuple[List[str], AuthorityLevel]] = [
        # CONSTITUTION: Highest authority - institutional law
        (
            ["rubric", "rubrica", "evaluation", "evaluacion", "reglamento", 
             "integridad", "integrity", "grading", "calificacion"],
            AuthorityLevel.CONSTITUTION
        ),
        # POLICY: Operational guidance
        (
            ["policy", "procedimiento", "procedure", "programa", "admin", "calendario", "calendar",
             "guia", "guide", "estructura", "structure", "horario"],
            AuthorityLevel.POLICY
        ),
        # CANONICAL: Official canonical materials
        (
            ["standard", "norma", "manual", "reference", "spec", "policy-manual",
             "oficial", "official", "aprobado", "approved"],
            AuthorityLevel.CANONICAL
        ),
    ]
    
    @classmethod
    def classify(
        cls, 
        storage_path: Optional[str] = None, 
        doc_type: Optional[str] = None,
        filename: Optional[str] = None
    ) -> AuthorityLevel:
        """
        Infers authority level from available context.
        
        Args:
            storage_path: Full storage path (e.g., "institutional/rubrics/math.pdf")
            doc_type: Document type from metadata (e.g., "rubric", "textbook")
            filename: Original filename for additional pattern matching
            
        Returns:
            AuthorityLevel enum value
        """
        # Combine all available text for pattern matching
        search_text = " ".join(filter(None, [
            storage_path.lower() if storage_path else None,
            doc_type.lower() if doc_type else None,
            filename.lower() if filename else None,
        ]))
        
        if not search_text:
            return AuthorityLevel.SUPPLEMENTARY
        
        # Check rules in priority order
        for patterns, authority in cls.CLASSIFICATION_RULES:
            if cls._matches_any_pattern(search_text, patterns):
                return authority
        
        # Default: lowest authority
        return AuthorityLevel.SUPPLEMENTARY
    
    @staticmethod
    def _matches_any_pattern(text: str, patterns: List[str]) -> bool:
        """Check if text contains any of the given patterns."""
        for pattern in patterns:
            # Use word boundary matching to avoid false positives
            if re.search(rf'\b{re.escape(pattern)}\b', text, re.IGNORECASE):
                return True
            # Also check without word boundaries for path segments
            if pattern in text:
                return True
        return False
