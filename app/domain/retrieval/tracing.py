from __future__ import annotations

import time
from typing import Any
from app.api.v1.schemas.retrieval_advanced import (
    ComprehensiveRetrievalRequest,
    ComprehensiveTrace,
    RetrievalItem,
)
from app.domain.retrieval.scoping import (
    extract_requested_standards, 
    normalize_scope_name, 
    extract_clause_refs
)
from app.domain.retrieval.fusion import missing_scopes, missing_clause_refs

def build_comprehensive_trace(
    *,
    request: ComprehensiveRetrievalRequest,
    merged_items: list[RetrievalItem],
    chunks_trace: dict[str, Any],
    graph_items: list[RetrievalItem],
    raptor_items: list[RetrievalItem],
    trace_warnings: list[str],
    hint_trace: dict[str, Any],
    policy_trace: dict[str, Any],
    min_score: float | None,
    noise_reduction: bool,
    started_at: float,
    missing_scopes_callback: Any, # We'll pass the method from service
    missing_clause_refs_callback: Any,
) -> ComprehensiveTrace:
    """Standardized trace builder for late fusion retrieval."""
    coverage = request.coverage_requirements
    query_scopes = list(extract_requested_standards(request.query))
    requested_scopes_raw = list(coverage.requested_standards) if coverage else []
    requested_scopes = [normalize_scope_name(scope) for scope in [*requested_scopes_raw, *query_scopes]]
    requested_scopes = [scope for scope in requested_scopes if scope]
    requested_scopes = list(dict.fromkeys(requested_scopes))
    require_all_scopes = bool(coverage.require_all_scopes) if coverage else len(requested_scopes) >= 2
    
    missing_scopes_after = missing_scopes(
        items=merged_items,
        requested_standards=requested_scopes,
        require_all_scopes=require_all_scopes,
    )
    
    query_clause_refs = list(extract_clause_refs(request.query))
    min_clause_refs_required = int(coverage.min_clause_refs) if coverage else 0
    missing_clause_refs_after = missing_clause_refs(
        items=merged_items,
        query_clause_refs=query_clause_refs,
        min_clause_refs_required=min_clause_refs_required,
    )

    base_trace_data = {
        **chunks_trace,
        "fusion": {
            "active": True,
            "quotas": {"chunks": 3, "graph": 2, "raptor": 1},
            "counts": {
                "chunks": len([i for i in merged_items if i.metadata.get("fusion_source") == "chunks"]),
                "graph": len(graph_items),
                "raptor": len(raptor_items)
            },
            "final_count": len(merged_items)
        },
        "warnings": list(dict.fromkeys(trace_warnings)),
        "timings_ms": {
            **(chunks_trace.get("timings_ms") or {}),
            "total": round((time.perf_counter() - started_at) * 1000, 2),
        },
        "coverage_repair_attempted": True,
        "coverage_repair_rounds": 1,
        "missing_scopes_before": [],
        "missing_scopes_after": missing_scopes_after,
        "missing_clause_refs_before": [],
        "missing_clause_refs_after": missing_clause_refs_after,
        "coverage_policy": {
            "requested_standards": requested_scopes,
            "require_all_scopes": require_all_scopes,
            "min_clause_refs": min_clause_refs_required,
            "graph_expansion_attempted": True,
            "graph_expansion_applied": True,
        },
        "retrieval_policy": {
            "min_score": min_score,
            "noise_reduction": noise_reduction,
            "search_hints_applied": hint_trace,
            "filtering": policy_trace,
        },
    }
    return ComprehensiveTrace.model_validate(base_trace_data)
