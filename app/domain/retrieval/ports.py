from __future__ import annotations
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from app.domain.schemas.knowledge_schemas import RAGSearchResult, RetrievalIntent

class IAtomicRetrievalRepository(Protocol):
    async def retrieve_hybrid_optimized(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def search_vectors_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def search_fts_only(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def fetch_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]: ...

class IAuthorityReranker(Protocol):
    def rerank(
        self, results: list[Any], intent: Any
    ) -> list[Any]: ...

class ISemanticReranker(Protocol):
    def is_enabled(self) -> bool: ...
    async def rerank_documents(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...

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
    async def resolve_summaries_to_chunk_ids(
        self,
        summary_ids: List[str]
    ) -> List[str]:
        ...

class IScopeResolverPolicy(ABC):
    @abstractmethod
    def extract_requested_scopes(self, query: str) -> tuple[str, ...]:
        pass
    @abstractmethod
    def has_ambiguous_reference(self, query: str) -> bool:
        pass
    @abstractmethod
    def suggest_scope_candidates(self, query: str) -> tuple[str, ...]:
        pass
    @abstractmethod
    def extract_item_scope(self, item: Dict[str, Any]) -> str:
        pass
