from __future__ import annotations

from app.mas_simple.domain.models import QueryIntent, RetrievalPlan


LITERAL_LIST_HINTS = (
    "entradas",
    "salidas",
    "lista",
    "exclusivas",
    "enumera",
    "listado",
    "viñetas",
)

LITERAL_NORMATIVE_HINTS = (
    "clausula",
    "cláusula",
    "documento obligatorio",
    "obligatorio",
    "exacto",
    "literal",
    "que exige",
    "qué exige",
)

COMPARATIVE_HINTS = ("compar", "difer", "vs", "ambas", "respecto")


def classify_intent(query: str) -> QueryIntent:
    text = (query or "").strip().lower()
    if any(h in text for h in LITERAL_LIST_HINTS):
        return QueryIntent(mode="literal_lista", rationale="list-like normative query")
    if any(h in text for h in LITERAL_NORMATIVE_HINTS):
        return QueryIntent(mode="literal_normativa", rationale="normative exactness query")
    if any(h in text for h in COMPARATIVE_HINTS):
        return QueryIntent(mode="comparativa", rationale="cross-standard comparison")
    return QueryIntent(mode="explicativa", rationale="general explanatory query")


def build_retrieval_plan(intent: QueryIntent) -> RetrievalPlan:
    if intent.mode in {"literal_lista", "literal_normativa"}:
        return RetrievalPlan(
            mode=intent.mode,
            chunk_k=45,
            chunk_fetch_k=220,
            summary_k=3,
            require_literal_evidence=True,
        )
    if intent.mode == "comparativa":
        return RetrievalPlan(
            mode=intent.mode,
            chunk_k=35,
            chunk_fetch_k=140,
            summary_k=5,
            require_literal_evidence=True,
        )
    return RetrievalPlan(
        mode=intent.mode,
        chunk_k=30,
        chunk_fetch_k=120,
        summary_k=5,
        require_literal_evidence=False,
    )
