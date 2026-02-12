from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.domain.knowledge_schemas import RAGSearchResult, RetrievalIntent

class IRetrievalRepository(ABC):
    """
    Interface for knowledge retrieval from the vector database.
    Decouples retrieval logic from infrastructure providers.
    """
    
    @abstractmethod
    async def match_knowledge(
        self, 
        vector: List[float], 
        filter_conditions: Dict[str, Any], 
        limit: int,
        query_text: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Executes semantic search against the database."""
        pass

    @abstractmethod
    async def match_knowledge_paginated(
        self,
        vector: List[float],
        filter_conditions: Dict[str, Any],
        limit: int,
        query_text: Optional[str] = None,
        cursor_score: Optional[float] = None,
        cursor_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Executes paginated semantic search with cursor-based pagination."""
        pass

    @abstractmethod
    async def match_summaries(
        self, 
        vector: List[float], 
        tenant_id: str, 
        limit: int,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves RAPTOR summary nodes."""
        pass
