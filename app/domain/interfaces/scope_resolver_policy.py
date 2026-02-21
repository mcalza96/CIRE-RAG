from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

class IScopeResolverPolicy(ABC):
    """
    Interface for domain-specific scope resolution logic. 
    Allows the GroundedRetrievalWorkflow to be agnostic of whether it's dealing 
    with ISO standards, financial regulations, or something else.
    """

    @abstractmethod
    def extract_requested_scopes(self, query: str) -> Tuple[str, ...]:
        """Extract explicit scope mentions from the query."""
        pass

    @abstractmethod
    def has_ambiguous_reference(self, query: str) -> bool:
        """Check if the query refers to a sub-element (e.g. a clause) without specifying the scope."""
        pass

    @abstractmethod
    def suggest_scope_candidates(self, query: str) -> Tuple[str, ...]:
        """Suggest potential scopes based on keywords in the query."""
        pass

    @abstractmethod
    def extract_item_scope(self, item: Dict[str, Any]) -> str:
        """Determine the scope of a specific retrieval result or document."""
        pass
