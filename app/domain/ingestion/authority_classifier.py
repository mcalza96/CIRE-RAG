"""
Authority Classification Service.

Infers AuthorityLevel based on file path patterns and document metadata.
Designed to be extensible - add new rules to CLASSIFICATION_RULES.
"""
import re
from typing import Optional, List, Tuple
import math
import hashlib
from collections import Counter

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

    EMBEDDING_PROTOTYPES = {
        AuthorityLevel.CONSTITUTION: "rubric integrity grading hard constraint reglamento evaluacion",
        AuthorityLevel.POLICY: "policy procedimiento guia calendario admin schedule",
        AuthorityLevel.CANONICAL: "standard norma manual oficial approved reference",
        AuthorityLevel.SUPPLEMENTARY: "supplementary note annex support extra",
    }
    
    @classmethod
    def classify(
        cls, 
        storage_path: Optional[str] = None, 
        doc_type: Optional[str] = None,
        filename: Optional[str] = None,
        mode: str = "rules",
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
        
        if str(mode).strip().lower() == "embedding_first":
            inferred = cls._classify_embedding_first(search_text)
            if inferred is not None:
                return inferred

        # Check rules in priority order
        for patterns, authority in cls.CLASSIFICATION_RULES:
            if cls._matches_any_pattern(search_text, patterns):
                return authority
        
        # Default: lowest authority
        return AuthorityLevel.SUPPLEMENTARY

    @classmethod
    def _classify_embedding_first(cls, text: str) -> Optional[AuthorityLevel]:
        if not text.strip():
            return None
        vec = cls._hash_embed(text)
        best: tuple[AuthorityLevel, float] | None = None
        for label, proto in cls.EMBEDDING_PROTOTYPES.items():
            score = cls._cosine(vec, cls._hash_embed(proto))
            if best is None or score > best[1]:
                best = (label, score)
        if best is None:
            return None
        return best[0]

    @staticmethod
    def _hash_embed(text: str, dim: int = 128) -> list[float]:
        tokens = re.findall(r"[a-zA-Z0-9áéíóúñ]+", text.lower())
        counts = Counter(tokens)
        vector = [0.0] * dim
        for token, weight in counts.items():
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            idx = int(digest[:8], 16) % dim
            vector[idx] += float(weight)
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        return sum(x * y for x, y in zip(a, b))
    
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
