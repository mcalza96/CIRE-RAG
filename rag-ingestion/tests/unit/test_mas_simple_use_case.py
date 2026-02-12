from dataclasses import dataclass
import asyncio

from app.mas_simple.application import HandleQuestionCommand, HandleQuestionUseCase
from app.mas_simple.domain.models import AnswerDraft, EvidenceItem, RetrievalPlan, ValidationResult


@dataclass
class _FakeRetriever:
    async def retrieve_chunks(self, query: str, tenant_id: str, collection_id: str | None, plan: RetrievalPlan):
        return [EvidenceItem(source="C1", content="[6.1.3 d)] Declaracion de aplicabilidad...")]

    async def retrieve_summaries(self, query: str, tenant_id: str, collection_id: str | None, plan: RetrievalPlan):
        return [EvidenceItem(source="R1", content="Resumen regulatorio")]


@dataclass
class _FakeAnswerGenerator:
    async def generate(self, query: str, scope_label: str, plan: RetrievalPlan, chunks, summaries):
        text = "6.1.3 d) | 'Declaracion de aplicabilidad' | Fuente(C1)"
        return AnswerDraft(text=text, mode=plan.mode, evidence=[*chunks, *summaries])


@dataclass
class _FakeValidator:
    def validate(self, draft: AnswerDraft, plan: RetrievalPlan):
        return ValidationResult(accepted=True, issues=[])


def test_use_case_executes_and_returns_validated_answer():
    use_case = HandleQuestionUseCase(
        retriever=_FakeRetriever(),
        answer_generator=_FakeAnswerGenerator(),
        validator=_FakeValidator(),
    )

    result = asyncio.run(
        use_case.execute(
            HandleQuestionCommand(
                query="Que documento obligatorio exige la clausula 6.1.3?",
                tenant_id="tenant-1",
                collection_id="collection-uuid",
                scope_label="tenant=tenant-1 / coleccion=iso",
            )
        )
    )

    assert result.intent.mode == "literal_normativa"
    assert result.plan.require_literal_evidence is True
    assert result.validation.accepted is True
    assert "Fuente(C1)" in result.answer.text
