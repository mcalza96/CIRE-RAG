from __future__ import annotations

from typing import Protocol

from app.mas_simple.domain.models import AnswerDraft, EvidenceItem, QueryIntent, RetrievalPlan, ValidationResult


class IntentClassifierPort(Protocol):
    def classify(self, query: str) -> QueryIntent:
        ...


class RetrieverPort(Protocol):
    async def retrieve_chunks(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        plan: RetrievalPlan,
    ) -> list[EvidenceItem]:
        ...

    async def retrieve_summaries(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        plan: RetrievalPlan,
    ) -> list[EvidenceItem]:
        ...


class AnswerGeneratorPort(Protocol):
    async def generate(
        self,
        query: str,
        scope_label: str,
        plan: RetrievalPlan,
        chunks: list[EvidenceItem],
        summaries: list[EvidenceItem],
    ) -> AnswerDraft:
        ...


class ValidationPort(Protocol):
    def validate(self, draft: AnswerDraft, plan: RetrievalPlan) -> ValidationResult:
        ...
