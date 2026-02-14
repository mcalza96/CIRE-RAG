from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TimeRangeFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    field: Literal["created_at", "updated_at"]
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class ScopeFilters(BaseModel):
    model_config = ConfigDict(extra="allow")

    metadata: dict[str, Any] | None = None
    time_range: TimeRangeFilter | None = None
    source_standard: str | None = None
    source_standards: list[str] | None = None


class RerankOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class GraphOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    relation_types: list[str] | None = None
    node_types: list[str] | None = None
    max_hops: int | None = Field(default=None, ge=0, le=4)


class HybridRetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    collection_id: str | None = None
    k: int = Field(default=12, ge=1, le=100)
    fetch_k: int = Field(default=60, ge=1, le=400)
    filters: ScopeFilters | None = None
    rerank: RerankOptions | None = None
    graph: GraphOptions | None = None


class SubQueryRequest(BaseModel):
    id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    k: int | None = Field(default=None, ge=1, le=100)
    fetch_k: int | None = Field(default=None, ge=1, le=400)
    filters: ScopeFilters | None = None


class MergeOptions(BaseModel):
    strategy: Literal["rrf"] = "rrf"
    rrf_k: int = Field(default=60, ge=1, le=500)
    top_k: int = Field(default=12, ge=1, le=100)


class MultiQueryRetrievalRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    collection_id: str | None = None
    queries: list[SubQueryRequest] = Field(..., min_length=1, max_length=8)
    merge: MergeOptions = Field(default_factory=MergeOptions)


class ExplainRetrievalRequest(HybridRetrievalRequest):
    top_n: int = Field(default=10, ge=1, le=50)


class ValidateScopeRequest(BaseModel):
    query: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    collection_id: str | None = None
    filters: ScopeFilters | None = None


class RetrievalItem(BaseModel):
    source: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class HybridTrace(BaseModel):
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    engine_mode: str = "hybrid"
    planner_used: bool = False
    planner_multihop: bool = False
    fallback_used: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class HybridRetrievalResponse(BaseModel):
    items: list[RetrievalItem]
    trace: HybridTrace


class SubQueryExecution(BaseModel):
    id: str
    status: Literal["ok", "error"]
    items_count: int = 0
    latency_ms: float = 0.0
    error_code: str | None = None
    error_message: str | None = None


class MultiQueryTrace(BaseModel):
    merge_strategy: str
    rrf_k: int
    failed_count: int
    timings_ms: dict[str, float] = Field(default_factory=dict)


class MultiQueryRetrievalResponse(BaseModel):
    items: list[RetrievalItem]
    subqueries: list[SubQueryExecution]
    partial: bool
    trace: MultiQueryTrace


class ScoreComponents(BaseModel):
    base_similarity: float
    jina_relevance_score: float | None = None
    final_score: float
    scope_penalized: bool = False
    scope_penalty_ratio: float | None = None


class RetrievalPath(BaseModel):
    source_layer: str | None = None
    source_type: str | None = None


class MatchedFilters(BaseModel):
    collection_id_match: bool | None = None
    time_range_match: bool | None = None
    metadata_keys_matched: list[str] = Field(default_factory=list)


class ExplainedItemDetails(BaseModel):
    score_components: ScoreComponents
    retrieval_path: RetrievalPath
    matched_filters: MatchedFilters


class ExplainedRetrievalItem(RetrievalItem):
    explain: ExplainedItemDetails


class ExplainTrace(HybridTrace):
    top_n: int


class ExplainRetrievalResponse(BaseModel):
    items: list[ExplainedRetrievalItem]
    trace: ExplainTrace


class ScopeIssue(BaseModel):
    code: str
    field: str
    message: str


class QueryScopeSummary(BaseModel):
    requested_standards: list[str] = Field(default_factory=list)
    requires_scope_clarification: bool = False
    suggested_scopes: list[str] = Field(default_factory=list)


class ValidateScopeResponse(BaseModel):
    valid: bool
    normalized_scope: dict[str, Any] = Field(default_factory=dict)
    violations: list[ScopeIssue] = Field(default_factory=list)
    warnings: list[ScopeIssue] = Field(default_factory=list)
    query_scope: QueryScopeSummary

