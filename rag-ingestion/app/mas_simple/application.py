from __future__ import annotations

from dataclasses import dataclass

from app.mas_simple.domain.models import AnswerDraft, QueryIntent, RetrievalPlan, ValidationResult
from app.mas_simple.domain.policies import build_retrieval_plan, classify_intent
from app.mas_simple.ports import AnswerGeneratorPort, RetrieverPort, ValidationPort


@dataclass(frozen=True)
class HandleQuestionCommand:
    query: str
    tenant_id: str
    collection_id: str | None
    scope_label: str


@dataclass(frozen=True)
class HandleQuestionResult:
    intent: QueryIntent
    plan: RetrievalPlan
    answer: AnswerDraft
    validation: ValidationResult


class HandleQuestionUseCase:
    """Application orchestrator for MAS Simple."""

    def __init__(
        self,
        retriever: RetrieverPort,
        answer_generator: AnswerGeneratorPort,
        validator: ValidationPort,
    ):
        self._retriever = retriever
        self._answer_generator = answer_generator
        self._validator = validator

    async def execute(self, cmd: HandleQuestionCommand) -> HandleQuestionResult:
        intent = classify_intent(cmd.query)
        plan = build_retrieval_plan(intent)

        chunks = await self._retriever.retrieve_chunks(
            query=cmd.query,
            tenant_id=cmd.tenant_id,
            collection_id=cmd.collection_id,
            plan=plan,
        )
        summaries = await self._retriever.retrieve_summaries(
            query=cmd.query,
            tenant_id=cmd.tenant_id,
            collection_id=cmd.collection_id,
            plan=plan,
        )

        answer = await self._answer_generator.generate(
            query=cmd.query,
            scope_label=cmd.scope_label,
            plan=plan,
            chunks=chunks,
            summaries=summaries,
        )
        validation = self._validator.validate(answer, plan)

        return HandleQuestionResult(
            intent=intent,
            plan=plan,
            answer=answer,
            validation=validation,
        )
