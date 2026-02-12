"""
Authority Level Taxonomy for semantic reranking.

This module defines the authority hierarchy used to prioritize 
institutional documents over generic web content during retrieval.
"""
from enum import Enum


class AuthorityLevel(str, Enum):
    """
    Ordered levels of semantic authority for RAG reranking.
    Higher levels represent "Immutable Truth" in the institutional context.
    
    Weights and reranking logic are managed centrally in the 
    TypeScript Gravity Matrix to ensure cross-agent consistency.
    """
    
    # Level 6: Hard Constraints - Immutable rules (e.g. "No religious content")
    HARD_CONSTRAINT = "hard_constraint"

    # Level 5: Administrative - System level overrides
    ADMINISTRATIVE = "administrative"

    # Level 4: Institutional law - official norms, controls, and integrity policies
    CONSTITUTION = "constitution"
    
    # Level 3: Operational guidance - procedures, calendars, operating structures
    POLICY = "policy"
    
    # Level 2: Canonical references - approved manuals and official materials
    CANONICAL = "canonical"
    
    # Level 1: Supplementary - Wikipedia, web articles, general notes
    SUPPLEMENTARY = "supplementary"

    @staticmethod
    def get_weight(level: 'AuthorityLevel') -> int:
        """Returns the numeric weight of a level for ordering/ranking."""
        weights = {
            AuthorityLevel.HARD_CONSTRAINT: 6,
            AuthorityLevel.ADMINISTRATIVE: 5,
            AuthorityLevel.CONSTITUTION: 4,
            AuthorityLevel.POLICY: 3,
            AuthorityLevel.CANONICAL: 2,
            AuthorityLevel.SUPPLEMENTARY: 1,
        }
        return weights.get(level, 0)
