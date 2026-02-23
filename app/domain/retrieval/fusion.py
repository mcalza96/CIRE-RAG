import math
import re
from typing import Any
from app.api.v1.schemas.retrieval_advanced import RetrievalItem
from app.domain.retrieval.scoping import extract_clause_refs, normalize_scope_name, extract_row_scope
from app.domain.retrieval.policies import filter_rows_by_min_score, reduce_structural_noise_rows

def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default

def extract_row(item: RetrievalItem) -> dict[str, Any]:
    """Helper to extract the original row dict from an item's metadata."""
    if not item.metadata:
        return {}
    row = item.metadata.get("row")
    if isinstance(row, dict):
        return row
    return item.metadata

def item_identity(item: RetrievalItem) -> str:
    """Deterministic identity for deduplication across sources."""
    row = extract_row(item)
    row_id = str(row.get("id") or "").strip()
    if row_id:
        return f"row::{row_id}"
    source = str(item.source or "").strip()
    content_key = str(item.content or "").strip()[:120]
    return f"fallback::{source}::{content_key}"

def item_clause_refs(item: RetrievalItem) -> set[str]:
    """Extract clause references from item content and metadata."""
    row = extract_row(item)
    refs: set[str] = set()
    raw_meta = row.get("metadata")
    metadata = raw_meta if isinstance(raw_meta, dict) else {}
    clause_id = str(metadata.get("clause_id") or row.get("clause_id") or "").strip()
    if clause_id:
        refs.add(clause_id)
    raw_clause_refs = metadata.get("clause_refs")
    if isinstance(raw_clause_refs, list):
        refs.update(str(val).strip() for val in raw_clause_refs if str(val).strip())
    refs.update(extract_clause_refs(str(item.content or "")))
    return refs

def fuse_late_results(
    *,
    chunks: list[RetrievalItem],
    graph: list[RetrievalItem],
    raptor: list[RetrievalItem],
    k: int,
) -> list[RetrievalItem]:
    """Assemble final results from parallel pipelines using strict quotas.
    
    Default Quotas:
    - Chunks: 3 positions
    - Graph Nodes: 2 positions
    - RAPTOR Summaries: 1 position
    
    Any remaining capacity in K is filled with chunks or available items.
    """
    quota_chunks = 3
    quota_graph = 2
    quota_raptor = 1
    
    merged: list[RetrievalItem] = []
    seen_identities: set[str] = set()

    def _add_items(source_items: list[RetrievalItem], limit: int):
        added = 0
        for item in source_items:
            if added >= limit:
                break
            identity = item_identity(item)
            if identity not in seen_identities:
                seen_identities.add(identity)
                merged.append(item)
                added += 1

    # 1. Fill Primary Quotas
    _add_items(chunks, quota_chunks)
    _add_items(graph, quota_graph)
    _add_items(raptor, quota_raptor)

    # 2. Sequential Overflow (fill up to k)
    if len(merged) < k:
        _add_items(chunks, k - len(merged))
    
    if len(merged) < k:
        _add_items(graph, k - len(merged))

    if len(merged) < k:
        _add_items(raptor, k - len(merged))
            
    return merged[:k]

def to_retrieval_items(rows: list[dict[str, Any]]) -> list[RetrievalItem]:
    """Convert raw rows to Pydantic RetrievalItem objects."""
    items: list[RetrievalItem] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
            
        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        
        # Preserve specific trace fields in metadata if they exist
        for key in (
            "source_layer", "source_type", "similarity",
            "jina_relevance_score", "scope_penalized",
            # Ownership fields — ensure they survive the row→RetrievalItem transform
            "tenant_id", "institution_id", "id",
        ):
            if key in row and (row[key] or key not in metadata):
                metadata[key] = row[key]

        items.append(
            RetrievalItem(
                source=str(row.get("id") or row.get("source") or f"R{idx + 1}"),
                content=content,
                score=_safe_float(row.get("score") or row.get("similarity")),
                metadata={
                    **metadata,
                    "source_layer": str(row.get("source_layer") or ""),
                    "source_type": str(row.get("source_type") or ""),
                    "similarity": _safe_float(row.get("similarity")),
                    "jina_relevance_score": _safe_float(row.get("jina_relevance_score")),
                    "scope_penalized": bool(row.get("scope_penalized", False)),
                },
            )
        )
    return items

def apply_retrieval_policy_to_items(
    items: list[RetrievalItem],
    *,
    min_score: float | None,
    noise_reduction: bool,
) -> tuple[list[RetrievalItem], dict[str, Any]]:
    """Apply min_score and noise reduction filters to items."""
    rows: list[dict[str, Any]] = []
    for item in items:
        row = {
            "source": item.source,
            "content": item.content,
            "score": float(item.score or 0.0),
            "similarity": float(item.score or 0.0),
            "metadata": dict(item.metadata or {}),
        }
        rows.append(row)

    policy_trace: dict[str, Any] = {}
    rows, min_score_trace = filter_rows_by_min_score(rows, min_score=min_score)
    policy_trace["min_score"] = min_score_trace

    if noise_reduction:
        rows, noise_trace = reduce_structural_noise_rows(rows)
        policy_trace["noise_reduction"] = noise_trace
    else:
        policy_trace["noise_reduction"] = {"applied": False, "reason": "disabled"}

    out_items: list[RetrievalItem] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        out_items.append(
            RetrievalItem(
                source=str(row.get("id") or row.get("source") or f"C{idx + 1}"),
                content=content,
                score=_safe_float(row.get("score"), default=0.0),
                metadata=metadata,
            )
        )
    return out_items, policy_trace

def missing_scopes(
    *,
    items: list[RetrievalItem],
    requested_standards: list[str],
    require_all_scopes: bool,
) -> list[str]:
    """Identify which requested standards are missing from the results."""
    if not require_all_scopes or not requested_standards:
        return []
    present: set[str] = set()
    for item in items:
        row = extract_row(item)
        scope = normalize_scope_name(extract_row_scope(row))
        if scope:
            present.add(scope)
    return [scope for scope in requested_standards if scope not in present]

def missing_clause_refs(
    *,
    items: list[RetrievalItem],
    query_clause_refs: list[str],
    min_clause_refs_required: int,
) -> list[str]:
    """Identify which required clauses are missing from the results."""
    if min_clause_refs_required <= 0 or not query_clause_refs:
        return []
    query_clause_set = {
        str(clause).strip() for clause in query_clause_refs if str(clause).strip()
    }
    if not query_clause_set:
        return []
    covered: set[str] = set()
    for item in items:
        covered.update(item_clause_refs(item))
    missing = [cl for cl in query_clause_refs if cl not in covered]
    missing = list(dict.fromkeys(missing))
    # Requirement: only return missing if the total count of covered is less than min_required
    if len(query_clause_set - covered) >= min_clause_refs_required:
         return missing
    return []

def stratify_results(
    results: list[dict], 
    requested_scopes: list[str] | tuple[str, ...], 
    max_count: int
) -> list[dict]:
    """Balance results across multiple requested standards (round-robin).
    
    Prevents a single standard from dominating the results (Annex SL Curse).
    """
    if not requested_scopes or not results:
        return results[:max_count]
    
    # Normalize requested scopes to ensure matching
    scope_targets = [normalize_scope_name(s) for s in requested_scopes if s]
    buckets = {s: [] for s in scope_targets}
    others = []
    
    for r in results:
        std = normalize_scope_name(extract_row_scope(r))
        matched = False
        for s in scope_targets:
            if std == s or s in std or std in s:
                buckets[s].append(r)
                matched = True
                break
        if not matched:
            others.append(r)
    
    stratified = []
    # Round-robin across buckets
    max_len = max([len(b) for b in buckets.values()] + [0])
    for i in range(max_len):
        for s in scope_targets:
            if i < len(buckets[s]):
                stratified.append(buckets[s][i])
            if len(stratified) >= max_count:
                break
        if len(stratified) >= max_count:
            break
                
    if len(stratified) < max_count:
        # Fill remaining with others
        stratified.extend(others[:max_count - len(stratified)])
            
    return stratified[:max_count]

def rrf_merge(
    grouped_items: list[tuple[str, list[RetrievalItem]]],
    *,
    rrf_k: int,
    top_k: int,
) -> list[RetrievalItem]:
    """Reciprocal Rank Fusion for merging results from multiple queries/sources."""
    score_by_id: dict[str, float] = {}
    item_by_id: dict[str, RetrievalItem] = {}
    seq = 0
    for _, items in grouped_items:
        seq += 1
        for rank, item in enumerate(items, start=1):
            row_id = item_identity(item)
            score_by_id[row_id] = score_by_id.get(row_id, 0.0) + (1.0 / (rrf_k + rank))
            if row_id not in item_by_id:
                item_by_id[row_id] = item

    ranked_ids = sorted(score_by_id.keys(), key=lambda key: score_by_id[key], reverse=True)
    merged: list[RetrievalItem] = []
    for row_id in ranked_ids[: max(1, top_k)]:
        source = item_by_id[row_id]
        merged.append(
            RetrievalItem(
                source=source.source,
                content=source.content,
                score=float(score_by_id[row_id]),
                metadata={
                    **(source.metadata or {}),
                    "score_space": "rrf",
                },
            )
        )
    return merged
