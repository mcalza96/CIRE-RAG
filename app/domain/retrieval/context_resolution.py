from typing import Any, Dict
from app.domain.retrieval.scoping import (
    clause_near_standard,
    extract_requested_standards,
    requested_scopes_from_context,
    normalize_standard_filters,
)
from app.domain.ingestion.metadata.metadata_enricher import enrich_metadata

def resolve_retrieval_filters(query: str, scope_context: Dict[str, Any]) -> Dict[str, Any]:
    """Resolves and merges filters from query text and scope context.
    
    Handles:
    - Tenant/Global scope determination
    - Collection ID resolution
    - Standard/Scope extraction (ISO 9001, etc)
    - Clause hint detection
    - Metadata enrichment from query
    """
    scope_type = scope_context.get("type", "institutional")
    filters: Dict[str, Any] = {}

    if scope_type == "institutional":
        tenant_id = scope_context.get("tenant_id")
        if not tenant_id:
            raise ValueError("Institutional scope requires 'tenant_id'.")
        filters["tenant_id"] = tenant_id
    elif scope_type == "global":
        filters["is_global"] = True

    extra = scope_context.get("filters", {})
    if isinstance(extra, dict) and extra:
        filters.update(extra)

    # Clean up any misplaced clause_id in nested filters
    nested_raw = filters.get("filters")
    nested_filters: Dict[str, Any] = nested_raw if isinstance(nested_raw, dict) else {}
    if "clause_id" in nested_filters:
        nested_filters = dict(nested_filters)
        nested_filters.pop("clause_id", None)
        if nested_filters:
            filters["filters"] = nested_filters
        else:
            filters.pop("filters", None)

    scope_collection = scope_context.get("collection_id")
    if scope_collection and not filters.get("collection_id"):
        filters["collection_id"] = scope_collection

    context_scopes = requested_scopes_from_context(scope_context)
    query_scopes = extract_requested_standards(query)
    effective_scopes = context_scopes if context_scopes else query_scopes
    clause_hint_allowed = len(effective_scopes) <= 1

    _, query_filters = enrich_metadata(query, {})
    if isinstance(query_filters, dict) and query_filters:
        safe_query_filters: Dict[str, Any] = {}
        for key, value in query_filters.items():
            key_str = str(key).strip()
            if not key_str or key_str in {"source_standard", "scope", "clause_refs", "clause_id"}:
                continue
            safe_query_filters[key_str] = value
        
        if safe_query_filters:
            nested_existing_raw = filters.get("filters")
            nested_existing: Dict[str, Any] = (
                nested_existing_raw if isinstance(nested_existing_raw, dict) else {}
            )
            nested_merged = dict(nested_existing)
            nested_merged.update(safe_query_filters)
            filters["filters"] = nested_merged

    if effective_scopes:
        standards = [str(scope).strip() for scope in effective_scopes if str(scope).strip()]
        standards = list(dict.fromkeys(standards))
        if standards:
            if len(standards) == 1:
                filters["source_standard"] = standards[0]
                filters.pop("source_standards", None)
            else:
                filters["source_standards"] = standards
                filters.pop("source_standard", None)

    if clause_hint_allowed:
        metadata_existing = filters.get("metadata")
        metadata = dict(metadata_existing) if isinstance(metadata_existing, dict) else {}
        if not str(metadata.get("clause_id") or "").strip():
            active_standard = str(filters.get("source_standard") or "").strip()
            clause_hint = (
                clause_near_standard(query, active_standard) if active_standard else None
            )
            if clause_hint:
                metadata["clause_id"] = clause_hint
        if metadata:
            filters["metadata"] = metadata

    return normalize_standard_filters(filters)
