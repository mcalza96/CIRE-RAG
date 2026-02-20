from __future__ import annotations

from dataclasses import dataclass


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
