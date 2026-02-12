from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.tools.retrieval import RetrievalTools
from app.mas_simple.domain.models import AnswerDraft, EvidenceItem, RetrievalPlan, ValidationResult


def _extract_keywords(query: str) -> set[str]:
    terms = set(re.findall(r"[a-zA-Z0-9áéíóúñÁÉÍÓÚÑ\.]{3,}", query.lower()))
    stop = {
        "para", "como", "cómo", "donde", "dónde", "sobre", "entre", "respecto",
        "tiene", "tienen", "debe", "deben", "norma", "normas", "iso", "clausula",
        "cláusula", "requisitos", "pregunta", "diferencia", "difiere", "ambas",
    }
    return {t for t in terms if t not in stop}


def _extract_clause_refs(query: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)+\b", (query or "")))


def _rerank_for_literal(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keywords = _extract_keywords(query)
    clause_refs = _extract_clause_refs(query)
    if not keywords and not clause_refs:
        return rows

    def score(row: dict[str, Any]) -> tuple[int, float]:
        content = str(row.get("content") or "").lower()
        overlap = sum(1 for kw in keywords if kw in content)
        clause_boost = sum(2 for ref in clause_refs if ref in content)
        similarity = float(row.get("similarity") or 0.0)
        return (overlap + clause_boost, similarity)

    return sorted(rows, key=score, reverse=True)


@dataclass
class RetrievalToolsAdapter:
    tools: RetrievalTools
    collection_name: str | None = None
    allowed_source_ids: set[str] | None = None

    async def retrieve_chunks(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        plan: RetrievalPlan,
    ) -> list[EvidenceItem]:
        scope_context: dict[str, Any] = {"type": "institutional", "tenant_id": tenant_id}
        if collection_id:
            scope_context["filters"] = {"collection_id": collection_id}
        elif self.collection_name:
            scope_context["filters"] = {"collection_name": self.collection_name}

        rows = await self.tools.retrieve(
            query=query,
            scope_context=scope_context,
            k=plan.chunk_k,
            fetch_k=plan.chunk_fetch_k,
            enable_reranking=True,
        )

        if self.allowed_source_ids:
            rows = [
                r for r in rows
                if str(r.get("source_id") or r.get("id") or "") in self.allowed_source_ids
            ]

        if plan.require_literal_evidence:
            rows = _rerank_for_literal(query, rows)

        return [
            EvidenceItem(
                source=f"C{i+1}",
                content=str(row.get("content") or "").strip(),
                score=float(row.get("similarity") or 0.0),
                metadata={"row": row},
            )
            for i, row in enumerate(rows)
            if row.get("content")
        ]

    async def retrieve_summaries(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        plan: RetrievalPlan,
    ) -> list[EvidenceItem]:
        try:
            rows = await self.tools.retrieve_summaries(
                query=query,
                tenant_id=tenant_id,
                k=plan.summary_k,
                collection_id=collection_id,
            )
        except Exception:
            rows = []

        if self.allowed_source_ids:
            filtered: list[dict[str, Any]] = []
            for row in rows:
                meta = row.get("metadata") or {}
                row_source = str(row.get("source_id") or row.get("id") or "")
                row_collection_id = str(meta.get("collection_id") or "")
                row_collection_name = str(meta.get("collection_name") or "")
                if row_source in self.allowed_source_ids:
                    filtered.append(row)
                    continue
                if collection_id and row_collection_id == str(collection_id):
                    filtered.append(row)
                    continue
                if self.collection_name and row_collection_name.lower() == str(self.collection_name).lower():
                    filtered.append(row)
            rows = filtered

        if plan.require_literal_evidence:
            rows = _rerank_for_literal(query, rows)

        return [
            EvidenceItem(
                source=f"R{i+1}",
                content=str(row.get("content") or "").strip(),
                score=float(row.get("similarity") or 0.0),
                metadata={"row": row},
            )
            for i, row in enumerate(rows)
            if row.get("content")
        ]


@dataclass
class GroqAnswerGeneratorAdapter:
    client: Any
    model_name: str

    async def generate(
        self,
        query: str,
        scope_label: str,
        plan: RetrievalPlan,
        chunks: list[EvidenceItem],
        summaries: list[EvidenceItem],
    ) -> AnswerDraft:
        if not chunks and not summaries:
            return AnswerDraft(
                text="⚠️ No pude encontrar información relevante en el contexto recuperado.",
                mode=plan.mode,
                evidence=[],
            )

        chunk_block = "\n\n".join(f"[{item.source}] {item.content}" for item in chunks)
        summary_block = "\n\n".join(f"[{item.source}] {item.content}" for item in summaries)
        context_parts: list[str] = []
        if summary_block:
            context_parts.append("=== RESUMENES (RAPTOR) ===\n" + summary_block)
        if chunk_block:
            context_parts.append("=== CHUNKS (DETALLE) ===\n" + chunk_block)
        context = "\n\n".join(context_parts)

        strict_literal = plan.mode in {"literal_normativa", "literal_lista", "comparativa"}
        if strict_literal:
            prompt = f"""
Eres un auditor de normas ISO. Contexto: {scope_label}

REGLAS:
1) Usa solo evidencia del contexto.
2) No inventes items ni sinonimos normativos.
3) Para cada afirmacion clave da: Clausula | Cita literal breve | Fuente(C# o R#).
4) Si falta evidencia: "No encontrado explicitamente en el contexto recuperado".
5) Prioriza precision sobre fluidez.

CONTEXTO:
{context}

PREGUNTA:
{query}

RESPUESTA:
"""
        else:
            prompt = f"""
Eres un analista experto. Contexto: {scope_label}

Usa solo informacion del contexto recuperado. Si no hay evidencia, dilo explicitamente.

CONTEXTO:
{context}

PREGUNTA:
{query}

RESPUESTA:
"""

        completion = await asyncio.to_thread(
            self.client.chat.completions.create,
            messages=[{"role": "user", "content": prompt}],
            model=self.model_name,
            temperature=0.05 if strict_literal else 0.3,
        )
        text = str(completion.choices[0].message.content or "").strip()
        return AnswerDraft(text=text, mode=plan.mode, evidence=[*chunks, *summaries])


class LiteralEvidenceValidator:
    def validate(self, draft: AnswerDraft, plan: RetrievalPlan) -> ValidationResult:
        issues: list[str] = []
        if plan.require_literal_evidence and not draft.evidence:
            issues.append("No retrieval evidence available for literal answer mode.")

        if plan.require_literal_evidence:
            has_citation_marker = "C" in draft.text or "R" in draft.text
            if not has_citation_marker:
                issues.append("Answer does not include explicit source markers (C#/R#).")

        return ValidationResult(accepted=not issues, issues=issues)
