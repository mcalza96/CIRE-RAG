from __future__ import annotations

from typing import Any, Protocol

from app.domain.schemas.knowledge_schemas import RAGSearchResult, RetrievalIntent


class IAuthorityReranker(Protocol):
    def rerank(
        self, results: list[RAGSearchResult], intent: RetrievalIntent
    ) -> list[RAGSearchResult]: ...


class ISemanticReranker(Protocol):
    def is_enabled(self) -> bool: ...

    async def rerank_documents(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[dict[str, Any]]: ...

    async def close(self) -> None: ...
