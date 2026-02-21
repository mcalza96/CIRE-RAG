"""
Canonical scope, standard, and clause utilities and service.

This module is the **single source of truth** for scope-related operations
used across the retrieval pipeline.
"""

from __future__ import annotations
import copy
import re
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional, Tuple

from app.infrastructure.settings import settings

# ── Compiled patterns (Agnostic, from settings) ───────────────────────────
_SCOPE_STANDARD_RE = re.compile(settings.SCOPE_EXTRACTION_REGEX, flags=re.IGNORECASE)
_CLAUSE_RE = re.compile(settings.SCOPE_AMBIGUITY_REGEX)
_CLAUSE_HINT_RE = re.compile(
    r"\b(cl(?:a|á)usula|clause|numeral|apartado|secci[oó]n|standard|scope)\b",
    flags=re.IGNORECASE,
)

# ── Standard extraction ──────────────────────────────────────────────────

def extract_requested_standards(query: str) -> tuple[str, ...]:
    """Return ordered, deduplicated scope labels found in *query*."""
    seen: set[str] = set()
    ordered: list[str] = []
    # Use finditer or re-eval the pattern to capture the full match
    for match in _SCOPE_STANDARD_RE.findall(query or ""):
        value = str(match).strip().upper()
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)

def extract_clause_refs(text: str) -> tuple[str, ...]:
    """Return ordered, deduplicated clause references from *text*."""
    return tuple(dict.fromkeys(_CLAUSE_RE.findall(text or "")))

# ── Scope key / normalisation ─────────────────────────────────────────────

def scope_key(value: str) -> str:
    """Normalise a scope label to a comparable key."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    
    # Generic normalization: remove spaces/special chars, keep alphanumeric
    # But try to capture the pattern first if it exists
    match = _SCOPE_STANDARD_RE.search(text)
    if match:
        return re.sub(r"[^A-Z0-9]", "-", match.group(0).strip().upper())
        
    compact = re.sub(r"[^A-Z0-9]", "", text)
    return compact

def normalize_scope_name(value: Any) -> str:
    """Normalise a raw scope/standard value to human-readable format."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = _SCOPE_STANDARD_RE.search(text)
    if match:
        return match.group(0).strip().upper()
    return text

def scope_clause_key(query: str, filters: Any = None) -> str:
    """Deterministic key for deduplicating identical subquery intents."""
    standard = normalize_scope_name(filters.source_standard if filters and hasattr(filters, "source_standard") else "")
    clause_id = ""
    if filters and hasattr(filters, "metadata") and isinstance(filters.metadata, dict):
        clause_id = str(filters.metadata.get("clause_id") or "").strip()
    
    if standard and clause_id:
        return f"scope_clause::{standard}::{clause_id}"
    
    normalized_query = re.sub(r"\s+", " ", str(query or "").strip().lower())
    return f"query::{normalized_query}"

# ── Row scope extraction ─────────────────────────────────────────────────

_SCOPE_CANDIDATE_KEYS = ("source_standard", "standard", "scope", "norma")

def extract_row_scope(item: dict[str, Any]) -> str:
    """Extract the scope label from any known nesting pattern."""
    meta_raw = item.get("metadata")
    metadata: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}

    candidates = [
        metadata.get("source_standard"),
        metadata.get("standard"),
        metadata.get("scope"),
        metadata.get("norma"),
        item.get("source_standard"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""

# ── Clause-near-standard detection ───────────────────────────────────────

def clause_near_standard(query: str, standard: str) -> str | None:
    """If exactly one clause ref appears near *standard* in *query*, return it."""
    q = str(query or "")
    s = str(standard or "").strip()
    if not q or not s:
        return None

    std_match = re.search(re.escape(s), q, flags=re.IGNORECASE)
    if not std_match:
        return None

    window_start = max(0, std_match.start() - 80)
    window_end = min(len(q), std_match.end() + 120)
    window = q[window_start:window_end]
    clauses = list(dict.fromkeys(_CLAUSE_RE.findall(window)))
    if len(clauses) == 1:
        return str(clauses[0])
    return None

def is_clause_heavy_query(query_text: str) -> bool:
    """Return ``True`` if the query mentions a clause hint keyword."""
    return bool(_CLAUSE_HINT_RE.search(query_text or ""))

# ── Requested scopes from context ────────────────────────────────────────

def requested_scopes_from_context(
    scope_context: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Extract the ordered, deduplicated tuple of requested standards."""
    if not isinstance(scope_context, dict):
        return ()

    values: list[str] = []

    # Top-level lists/single values
    raw_list = scope_context.get("source_standards")
    if isinstance(raw_list, list):
        values.extend(str(v).strip() for v in raw_list if isinstance(v, str) and str(v).strip())

    single = scope_context.get("source_standard")
    if isinstance(single, str) and single.strip():
        values.append(single.strip())

    # Nested ``filters`` dict
    nested_raw = scope_context.get("filters")
    nested = nested_raw if isinstance(nested_raw, dict) else {}

    nested_list = nested.get("source_standards")
    if isinstance(nested_list, list):
        values.extend(str(v).strip() for v in nested_list if isinstance(v, str) and str(v).strip())

    nested_single = nested.get("source_standard")
    if isinstance(nested_single, str) and nested_single.strip():
        values.append(nested_single.strip())

    if not values:
        return ()
    return tuple(dict.fromkeys(values))

def normalize_standard_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Standardize source_standard vs source_standards in filter dictionaries."""
    normalized: dict[str, Any] = dict(filters)
    standards_raw = normalized.get("source_standards")
    standards: list[str] = []
    if isinstance(standards_raw, list):
        standards = [
            str(item).strip()
            for item in standards_raw
            if isinstance(item, str) and str(item).strip()
        ]
        standards = list(dict.fromkeys(standards))
    
    single = str(normalized.get("source_standard") or "").strip()
    if single and single not in standards:
        standards.insert(0, single)

    if len(standards) > 1:
        normalized["source_standards"] = standards
        normalized.pop("source_standard", None)
    elif len(standards) == 1:
        normalized["source_standard"] = standards[0]
        normalized.pop("source_standards", None)
    else:
        normalized.pop("source_standard", None)
        normalized.pop("source_standards", None)

    return normalized

# ── Scope penalty helpers ────────────────────────────────────────────────

def apply_scope_penalty(
    results: list[dict[str, Any]],
    requested_scopes: tuple[str, ...],
    *,
    penalty_factor: float = 0.75,
) -> list[dict[str, Any]]:
    """Down-weight items whose scope doesn't match any *requested_scopes*."""
    if not requested_scopes:
        return results

    requested_keys = {scope_key(s) for s in requested_scopes if scope_key(s)}

    reranked: list[dict[str, Any]] = []
    for row in results:
        row_scope = extract_row_scope(row)
        if not row_scope:
            reranked.append(row)
            continue

        row_scope_key = scope_key(row_scope)
        row_scope_upper = row_scope.upper()
        if row_scope_key and row_scope_key in requested_keys:
            reranked.append(row)
            continue
        if any(str(scope).upper() in row_scope_upper for scope in requested_scopes):
            reranked.append(row)
            continue

        adjusted = dict(row)
        base_similarity = float(
            adjusted.get(
                "jina_relevance_score",
                adjusted.get("similarity", adjusted.get("score", 0.0)),
            )
            or 0.0
        )
        penalized = max(base_similarity * (1.0 - penalty_factor), 0.0)
        adjusted["scope_penalized"] = True
        adjusted["scope_penalty"] = penalty_factor
        adjusted["similarity"] = penalized
        adjusted["score"] = penalized
        adjusted["jina_relevance_score"] = penalized
        reranked.append(adjusted)

    return reranked

def count_scope_penalized(results: list[dict[str, Any]]) -> int:
    """Count how many items have been scope-penalised."""
    return sum(1 for item in results if bool(item.get("scope_penalized")))

def scope_penalty_ratio(
    rows: list[dict[str, Any]],
    requested_scopes: tuple[str, ...],
) -> float:
    """Fraction of scoped rows whose scope does NOT match any request."""
    if not requested_scopes or not rows:
        return 0.0

    requested_upper = {scope.upper() for scope in requested_scopes}
    considered = 0
    penalized = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_scope = extract_row_scope(row)
        if not row_scope:
            continue
        considered += 1
        if not any(scope in row_scope for scope in requested_upper):
            penalized += 1
    if considered == 0:
        return 0.0
    return penalized / considered

class RetrievalScopeService:
    """
    Handles scope enforcement, structural filtering, and tenant context stamping 
    for retrieval results.
    """

    @staticmethod
    def requested_scopes(scope_context: Dict[str, Any] | None) -> Tuple[str, ...]:
        return requested_scopes_from_context(scope_context)

    @staticmethod
    def scope_penalty_ratio(rows: List[Dict[str, Any]], requested_scopes: Tuple[str, ...]) -> float:
        return scope_penalty_ratio(rows, requested_scopes)

    def scope_context_for_subquery(
        self,
        *,
        scope_context: Dict[str, Any] | None,
        subquery_text: str,
    ) -> Dict[str, Any] | None:
        if not isinstance(scope_context, dict):
            return scope_context

        scoped: Dict[str, Any] = copy.deepcopy(scope_context)
        standards = list(extract_requested_standards(subquery_text or ""))
        clauses = list(extract_clause_refs(subquery_text or ""))

        nested_raw = scoped.get("filters")
        nested: Dict[str, Any] = dict(nested_raw) if isinstance(nested_raw, dict) else {}

        if standards:
            if len(standards) == 1:
                scoped["source_standard"] = standards[0]
                scoped.pop("source_standards", None)
                nested["source_standard"] = standards[0]
                nested.pop("source_standards", None)
            else:
                scoped["source_standards"] = standards
                scoped.pop("source_standard", None)
                nested["source_standards"] = standards
                nested.pop("source_standard", None)

        if clauses:
            active_standard = str(
                scoped.get("source_standard") or nested.get("source_standard") or ""
            ).strip()
            clause_for_standard = (
                clause_near_standard(subquery_text, active_standard) if active_standard else None
            )
            if active_standard and clause_for_standard:
                metadata_raw = nested.get("metadata")
                metadata: Dict[str, Any] = (
                    dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
                )
                metadata["clause_id"] = clause_for_standard
                nested["metadata"] = metadata
            else:
                metadata_raw = nested.get("metadata")
                metadata: Dict[str, Any] = (
                    dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
                )
                metadata.pop("clause_id", None)
                if metadata:
                    nested["metadata"] = metadata
                else:
                    nested.pop("metadata", None)
            
        scoped["filters"] = nested
        return scoped

    @staticmethod
    def stamp_tenant_context(
        *, rows: List[Dict[str, Any]], tenant_id: str, allowed_source_ids: set[str]
    ) -> None:
        """Attach tenant ownership metadata for rows."""
        if not tenant_id:
            return

        for row in rows:
            if not isinstance(row, dict):
                continue
            meta_raw = row.get("metadata")
            metadata: Dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
            if meta_raw is None or not isinstance(meta_raw, dict):
                row["metadata"] = metadata

            source_layer = str(row.get("source_layer") or "").strip().lower()
            source_id = str(metadata.get("source_id") or "").strip()
            safe_to_stamp = False
            
            # If layer is vector/fts/hybrid, verify source_id belongs to tenant
            if (
                source_layer in {"vector", "fts", "hybrid"}
                and source_id
                and source_id in allowed_source_ids
            ):
                safe_to_stamp = True
            
            # Graph layer is already tenant-scoped
            if source_layer == "graph":
                safe_to_stamp = True

            if not safe_to_stamp:
                continue

            row.setdefault("institution_id", tenant_id)
            row.setdefault("tenant_id", tenant_id)
            metadata.setdefault("institution_id", tenant_id)
            metadata.setdefault("tenant_id", tenant_id)

    @classmethod
    def filter_structural_rows(
        cls, items: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not items:
            return [], {"applied": True, "dropped": 0, "kept": 0}
        kept: List[Dict[str, Any]] = []
        dropped = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if cls._is_structural_only_row(item):
                dropped += 1
                continue
            kept.append(item)
        return kept, {"applied": True, "dropped": dropped, "kept": len(kept)}

    @classmethod
    def _is_structural_only_row(cls, item: Dict[str, Any]) -> bool:
        metadata = cls._get_merged_metadata(item)
        if metadata.get("retrieval_eligible") is False:
            return True
        if metadata.get("is_toc") is True:
            return True
        if metadata.get("is_frontmatter") is True:
            return True
        return False

    @staticmethod
    def _get_merged_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        raw = item.get("metadata")
        metadata: Dict[str, Any] = raw if isinstance(raw, dict) else {}
        nested_row = metadata.get("row")
        if not isinstance(nested_row, dict):
            return metadata
        nested_meta = nested_row.get("metadata")
        if not isinstance(nested_meta, dict):
            return metadata
        merged = dict(nested_meta)
        merged.update(metadata)
        return merged

    @staticmethod
    def matches_metadata_filters(metadata: Dict[str, Any], expected: Dict[str, Any]) -> bool:
        for key, value in expected.items():
            observed = metadata.get(str(key))
            if isinstance(value, list):
                if observed not in value:
                    return False
                continue
            if observed != value:
                return False
        return True

    @classmethod
    def matches_time_range(cls, row: Dict[str, Any], time_range: Dict[str, Any]) -> bool:
        field = str(time_range.get("field") or "").strip()
        if field not in {"created_at", "updated_at"}:
            return False
        row_value = row.get(field)
        if not isinstance(row_value, str) or not row_value.strip():
            return False
        try:
            row_dt = cls._parse_iso8601(row_value)
            dt_from = cls._parse_iso8601(time_range.get("from"))
            dt_to = cls._parse_iso8601(time_range.get("to"))
        except Exception:
            return False
        if row_dt is None:
            return False
        if dt_from and row_dt < dt_from:
            return False
        if dt_to and row_dt > dt_to:
            return False
        return True

    @staticmethod
    def _parse_iso8601(value: str | None) -> datetime | None:
        if not value: return None
        try:
            text = str(value).strip()
            if not text: return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None
