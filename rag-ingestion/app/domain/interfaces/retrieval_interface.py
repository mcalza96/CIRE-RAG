from typing import Any, Dict, List, Optional, Protocol

class IRetrievalRepository(Protocol):
    async def match_knowledge(
        self,
        vector: List[float],
        filter_conditions: Dict[str, Any],
        limit: int,
        query_text: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...

    async def match_knowledge_paginated(
        self,
        vector: List[float],
        filter_conditions: Dict[str, Any],
        limit: int,
        query_text: Optional[str] = None,
        cursor_score: Optional[float] = None,
        cursor_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...

    async def match_summaries(
        self,
        vector: List[float],
        tenant_id: str,
        limit: int,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ...
