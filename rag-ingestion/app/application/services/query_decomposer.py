from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_llm
from app.core.settings import settings

logger = structlog.get_logger(__name__)


@dataclass
class PlannedSubQuery:
    id: int
    query: str
    dependency_id: int | None = None


@dataclass
class QueryPlan:
    is_multihop: bool
    execution_mode: str
    sub_queries: list[PlannedSubQuery]
    fallback_reason: str | None = None


class QueryDecomposer:
    """Single-shot low-latency query planner for multi-hop retrieval."""

    def __init__(self, llm_provider: BaseChatModel | None = None, timeout_ms: int | None = None):
        self._llm = llm_provider or get_llm(temperature=0.0, capability="ORCHESTRATION", prefer_provider="groq")
        self._timeout_ms = int(timeout_ms or settings.QUERY_DECOMPOSER_TIMEOUT_MS)

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
            payload = self._parse_payload(str(response.content))
            return self._normalize_plan(payload=payload, original_query=query)
        except asyncio.TimeoutError:
            logger.info("query_decomposer_timeout", timeout_ms=self._timeout_ms)
            return QueryPlan(
                is_multihop=False,
                execution_mode="parallel",
                sub_queries=[PlannedSubQuery(id=1, query=query)],
                fallback_reason="timeout",
            )
        except Exception as exc:
            logger.warning("query_decomposer_failed", error=str(exc))
            return QueryPlan(
                is_multihop=False,
                execution_mode="parallel",
                sub_queries=[PlannedSubQuery(id=1, query=query)],
                fallback_reason="error",
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a retrieval planner. Return strict JSON only. "
            "Do not answer the user query. "
            "If query requires dependencies between facts, set is_multihop=true. "
            "Output schema: "
            "{\"is_multihop\": boolean, \"execution_mode\": \"parallel\"|\"sequential\", "
            "\"sub_queries\": [{\"id\": int, \"query\": string, \"dependency_id\": int|null}]}. "
            "Keep sub_queries concise and optimized for retrieval."
        )

    @staticmethod
    def _parse_payload(content: str) -> dict[str, Any]:
        raw = (content or "").strip()
        if not raw:
            raise ValueError("empty planner output")

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("planner output is not valid JSON object")

    @staticmethod
    def _normalize_plan(payload: dict[str, Any], original_query: str) -> QueryPlan:
        is_multihop = bool(payload.get("is_multihop", False))
        raw_mode = str(payload.get("execution_mode", "parallel")).strip().lower()
        execution_mode = "sequential" if raw_mode == "sequential" else "parallel"

        raw_sub_queries = payload.get("sub_queries")
        sub_queries: list[PlannedSubQuery] = []
        if isinstance(raw_sub_queries, list):
            for idx, item in enumerate(raw_sub_queries[:6], start=1):
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
                sub_queries.append(PlannedSubQuery(id=qid, query=raw_query, dependency_id=dep_id))

        if not sub_queries:
            sub_queries = [PlannedSubQuery(id=1, query=original_query)]
            is_multihop = False
            execution_mode = "parallel"

        if not is_multihop and len(sub_queries) > 1:
            sub_queries = [sub_queries[0]]

        return QueryPlan(
            is_multihop=is_multihop,
            execution_mode=execution_mode,
            sub_queries=sub_queries,
        )
