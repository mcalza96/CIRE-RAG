from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_llm
from app.core.settings import settings

logger = structlog.get_logger(__name__)


_MULTIHOP_CUES_RE = re.compile(
    r"\b("
    r"compara(?:r|cion)?|diferenc(?:ia|ias)|versus|vs\.?|"
    r"contrasta|relaciona|impacto|causa|efecto|"
    r"resume\s+y\s+compara|"
    r"adem[aá]s|junto con|por otro lado|"
    r"y\s+qu[eé]|y\s+cu[aá]l|y\s+c[oó]mo"
    r")\b",
    flags=re.IGNORECASE,
)
_STD_RE = re.compile(r"\biso\s*[-:]?\s*(\d{4,5})\b", flags=re.IGNORECASE)
_CLAUSE_RE = re.compile(r"\b\d+(?:\.\d+)+\b")


def is_simple_single_hop_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if len(text) > 220 or "\n" in text:
        return False
    if any(sep in text for sep in (";", "|")):
        return False
    if text.count(",") >= 2:
        return False
    if _MULTIHOP_CUES_RE.search(text):
        return False

    standards = re.findall(r"\b(?:ISO|IEC)\s*\d{3,5}\b", text, flags=re.IGNORECASE)
    if len({s.upper().replace(" ", "") for s in standards}) > 1:
        return False

    lowered = text.lower()
    direct_intent = any(
        cue in lowered
        for cue in (
            "que dice",
            "qué dice",
            "que exige",
            "qué exige",
            "exige textualmente",
            "texto literal",
            "enumera",
            "lista de",
            "listado de",
            "entradas requeridas",
            "salidas esperadas",
            "que establece",
            "qué establece",
            "que indica",
            "qué indica",
            "introduccion",
            "introducción",
            "clausula",
            "cláusula",
            "resumen de",
            "explica",
        )
    )
    if not direct_intent:
        return False

    # If the query is scoped to a single standard and has no multihop cues,
    # skip decomposition to save latency.
    return True


@dataclass
class PlannedSubQuery:
    id: int
    query: str
    dependency_id: int | None = None
    target_relations: list[str] | None = None
    target_node_types: list[str] | None = None
    is_deep: bool = False


@dataclass
class QueryPlan:
    is_multihop: bool
    execution_mode: str
    sub_queries: list[PlannedSubQuery]
    fallback_reason: str | None = None


class QueryDecomposer:
    """Single-shot low-latency query planner for multi-hop retrieval."""

    def __init__(self, llm_provider: BaseChatModel | None = None, timeout_ms: int | None = None):
        self._llm = llm_provider or get_llm(
            temperature=0.0, capability="ORCHESTRATION", prefer_provider="groq"
        )
        self._timeout_ms = int(timeout_ms or settings.QUERY_DECOMPOSER_TIMEOUT_MS)
        self._max_subqueries = max(
            1, int(getattr(settings, "QUERY_DECOMPOSER_MAX_SUBQUERIES", 4) or 4)
        )

    @staticmethod
    def _multihop_tolerance() -> float:
        try:
            value = float(getattr(settings, "QUERY_DECOMPOSER_MULTIHOP_TOLERANCE", 0.55) or 0.55)
        except (TypeError, ValueError):
            value = 0.55
        return max(0.0, min(1.0, value))

    @staticmethod
    def _estimate_multihop_signal(
        *,
        query: str,
        sub_queries_count: int,
        model_marked_multihop: bool,
    ) -> float:
        text = str(query or "")
        standards = set(_STD_RE.findall(text))
        clauses = set(_CLAUSE_RE.findall(text))

        score = 0.0
        if model_marked_multihop:
            score += 0.2
        if sub_queries_count >= 2:
            score += 0.25
        if len(standards) >= 2:
            score += 0.35
        elif len(standards) == 1 and len(clauses) >= 2:
            score += 0.15
        if len(clauses) >= 3:
            score += 0.15
        elif len(clauses) == 2:
            score += 0.1
        if _MULTIHOP_CUES_RE.search(text):
            score += 0.2
        return round(max(0.0, min(1.0, score)), 4)

    async def decompose(self, query: str) -> QueryPlan:
        if not query.strip():
            return QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])

        try:
            response = await asyncio.wait_for(
                self._llm.ainvoke(
                    [
                        {"role": "system", "content": self._system_prompt()},
                        {"role": "user", "content": query},
                    ]
                ),
                timeout=max(self._timeout_ms, 100) / 1000.0,
            )
            response_text = self._response_to_text(response)
            payload = self._parse_payload(response_text)
            return self._normalize_plan(payload=payload, original_query=query)
        except asyncio.TimeoutError:
            logger.info("query_decomposer_timeout", timeout_ms=self._timeout_ms)
            return self._deterministic_fallback_plan(query, reason="timeout")
        except Exception as exc:
            logger.warning(
                "query_decomposer_failed",
                error=str(exc),
            )
            return self._deterministic_fallback_plan(query, reason="error")

    def _deterministic_fallback_plan(self, query: str, *, reason: str) -> QueryPlan:
        standards = [f"ISO {m}" for m in _STD_RE.findall(query or "")]
        standards = list(dict.fromkeys(standards))
        clauses = list(dict.fromkeys(_CLAUSE_RE.findall(query or "")))

        sub_queries: list[PlannedSubQuery] = []
        next_id = 1
        for std in standards[: self._max_subqueries]:
            clause = clauses[next_id - 1] if next_id - 1 < len(clauses) else ""
            text = " ".join(part for part in [std, clause, query] if part).strip()
            sub_queries.append(PlannedSubQuery(id=next_id, query=text[:900]))
            next_id += 1

        if not sub_queries:
            sub_queries = [PlannedSubQuery(id=1, query=query)]

        is_multihop = len(sub_queries) > 1
        return QueryPlan(
            is_multihop=is_multihop,
            execution_mode="parallel",
            sub_queries=sub_queries[: self._max_subqueries],
            fallback_reason=f"deterministic_{reason}",
        )

    @staticmethod
    def _response_to_text(response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            text = content.get("text") or content.get("content")
            return str(text or "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str) and text.strip():
                        parts.append(text)
            return "\n".join(part for part in parts if part).strip()
        return str(content or "")

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a retrieval planner. Return strict JSON only. "
            "Do not answer the user query. "
            "If query requires dependencies between facts, set is_multihop=true. "
            "Output schema: "
            '{"is_multihop": boolean, "execution_mode": "parallel"|"sequential", '
            '"sub_queries": [{"id": int, "query": string, "dependency_id": int|null, '
            '"target_relations": string[]|null, "target_node_types": string[]|null, "is_deep": boolean}]}. '
            "Use target_relations when relation traversal is explicit (e.g., prerequisite, misconception_of, remedies). "
            "Use target_node_types when node type is explicit (e.g., competency, misconception, bridge). "
            "Keep sub_queries concise and optimized for retrieval."
        )

    @staticmethod
    def _parse_payload(content: str) -> dict[str, Any]:
        raw = (content or "").strip()
        if not raw:
            raise ValueError("empty planner output")

        candidates: list[str] = [raw]
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE):
            inner = (match.group(1) or "").strip()
            if inner:
                candidates.append(inner)

        if raw.lower().startswith("json"):
            stripped = raw[4:].strip()
            if stripped:
                candidates.append(stripped)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue

            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(candidate[start : end + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue

        raise ValueError("planner output is not valid JSON object")

    @classmethod
    def _normalize_plan(cls, payload: dict[str, Any], original_query: str) -> QueryPlan:
        def infer_graph_controls(
            query_text: str,
        ) -> tuple[list[str] | None, list[str] | None, bool]:
            text = (query_text or "").strip().lower()

            relation_map: dict[str, tuple[str, ...]] = {
                "prerequisite": ("prerrequis", "prerequis", "previo", "base de", "depends on"),
                "misconception_of": ("error", "misconcep", "confusi", "malentendido"),
                "remedies": ("remedio", "correg", "intervenci", "refuerzo", "mitigar"),
            }
            node_map: dict[str, tuple[str, ...]] = {
                "competency": ("competenc", "habilidad", "skill"),
                "misconception": ("misconcep", "error", "confusi", "malentendido"),
                "bridge": ("puente", "bridge", "conexi", "vincul"),
            }

            relations: list[str] = [
                rel for rel, hints in relation_map.items() if any(h in text for h in hints)
            ]
            node_types: list[str] = [
                node for node, hints in node_map.items() if any(h in text for h in hints)
            ]

            deep_markers = (
                "causa",
                "impact",
                "relacion",
                "relación",
                "depende",
                "cadena",
                "how",
                "por que",
                "por qué",
            )
            is_deep = any(marker in text for marker in deep_markers)

            return (relations or None, node_types or None, is_deep)

        is_multihop = bool(payload.get("is_multihop", False))
        raw_mode = str(payload.get("execution_mode", "parallel")).strip().lower()
        execution_mode = "sequential" if raw_mode == "sequential" else "parallel"

        raw_sub_queries = payload.get("sub_queries")
        sub_queries: list[PlannedSubQuery] = []
        if isinstance(raw_sub_queries, list):
            max_subqueries = max(
                1, int(getattr(settings, "QUERY_DECOMPOSER_MAX_SUBQUERIES", 4) or 4)
            )
            for idx, item in enumerate(raw_sub_queries[:max_subqueries], start=1):
                if not isinstance(item, dict):
                    continue
                raw_query = str(item.get("query") or "").strip()
                if not raw_query:
                    continue
                raw_id = item.get("id")
                if isinstance(raw_id, int):
                    qid = raw_id
                elif isinstance(raw_id, str) and raw_id.strip().isdigit():
                    qid = int(raw_id.strip())
                else:
                    qid = idx
                dep = item.get("dependency_id")
                dep_id: int | None = None
                if isinstance(dep, int):
                    dep_id = dep
                elif isinstance(dep, str) and dep.strip().isdigit():
                    dep_id = int(dep.strip())

                raw_relations = item.get("target_relations")
                target_relations: list[str] | None = None
                if isinstance(raw_relations, list):
                    target_relations = [
                        str(value).strip()
                        for value in raw_relations
                        if isinstance(value, str) and str(value).strip()
                    ] or None

                raw_node_types = item.get("target_node_types")
                target_node_types: list[str] | None = None
                if isinstance(raw_node_types, list):
                    target_node_types = [
                        str(value).strip()
                        for value in raw_node_types
                        if isinstance(value, str) and str(value).strip()
                    ] or None

                is_deep = bool(item.get("is_deep", False))
                inferred_relations, inferred_node_types, inferred_deep = infer_graph_controls(
                    raw_query
                )
                if target_relations is None:
                    target_relations = inferred_relations
                if target_node_types is None:
                    target_node_types = inferred_node_types
                if not is_deep:
                    is_deep = inferred_deep

                sub_queries.append(
                    PlannedSubQuery(
                        id=qid,
                        query=raw_query,
                        dependency_id=dep_id,
                        target_relations=target_relations,
                        target_node_types=target_node_types,
                        is_deep=is_deep,
                    )
                )

            if len(sub_queries) > max_subqueries:
                sub_queries = sub_queries[:max_subqueries]

        if not sub_queries:
            fallback_relations, fallback_node_types, fallback_deep = infer_graph_controls(
                original_query
            )
            sub_queries = [
                PlannedSubQuery(
                    id=1,
                    query=original_query,
                    target_relations=fallback_relations,
                    target_node_types=fallback_node_types,
                    is_deep=fallback_deep,
                )
            ]
            is_multihop = False
            execution_mode = "parallel"

        if not is_multihop and len(sub_queries) > 1:
            sub_queries = [sub_queries[0]]

        fallback_reason: str | None = None
        if is_multihop and len(sub_queries) > 1:
            multihop_signal = cls._estimate_multihop_signal(
                query=original_query,
                sub_queries_count=len(sub_queries),
                model_marked_multihop=bool(payload.get("is_multihop", False)),
            )
            tolerance = cls._multihop_tolerance()
            if multihop_signal < tolerance:
                sub_queries = [sub_queries[0]]
                is_multihop = False
                execution_mode = "parallel"
                fallback_reason = "multihop_below_tolerance"

        return QueryPlan(
            is_multihop=is_multihop,
            execution_mode=execution_mode,
            sub_queries=sub_queries,
            fallback_reason=fallback_reason,
        )
