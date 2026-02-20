"""
Canonical scope, standard, and clause utilities.

This module is the **single source of truth** for scope-related operations
used across the retrieval pipeline.  Every class that previously carried its
own copy (RetrievalBroker, AtomicRetrievalEngine, RetrievalContractService)
MUST import from here.  Do NOT duplicate this logic elsewhere.
"""

from __future__ import annotations

import re
from typing import Any

# ── Compiled patterns (reused everywhere) ────────────────────────────────
_ISO_STANDARD_RE = re.compile(r"\biso\s*[-:]?\s*(\d{4,5})\b", flags=re.IGNORECASE)
_CLAUSE_RE = re.compile(r"\b\d+(?:\.\d+)+\b")
_CLAUSE_HINT_RE = re.compile(
    r"\b(cl(?:a|á)usula|clause|numeral|apartado|secci[oó]n)\b",
    flags=re.IGNORECASE,
)


# ── Standard extraction ──────────────────────────────────────────────────


def extract_requested_standards(query: str) -> tuple[str, ...]:
    """Return ordered, deduplicated ``ISO NNNNN`` labels found in *query*."""
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _ISO_STANDARD_RE.findall(query or ""):
        value = f"ISO {match}"
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def extract_clause_refs(text: str) -> tuple[str, ...]:
    """Return ordered, deduplicated clause references from *text*."""
    return tuple(dict.fromkeys(_CLAUSE_RE.findall(text or "")))


# ── Scope key / normalisation ─────────────────────────────────────────────


def scope_key(value: str) -> str:
    """Normalise a scope label to a comparable key like ``ISO-45001``."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    iso_match = re.search(r"\bISO\s*[-:]?\s*(\d{4,5})\b", text, flags=re.IGNORECASE)
    if iso_match:
        return f"ISO-{iso_match.group(1)}"
    digits_match = re.search(r"\b(\d{4,5})\b", text)
    if digits_match:
        return f"ISO-{digits_match.group(1)}"
    compact = re.sub(r"[^A-Z0-9]", "", text)
    return compact


def normalize_scope_name(value: Any) -> str:
    """Normalise a raw scope/standard value to human-readable ``ISO NNNNN``."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\bISO\s*[-:]?\s*(\d{4,5})\b", text, flags=re.IGNORECASE)
    if match:
        return f"ISO {match.group(1)}"
    digits = re.search(r"\b(\d{4,5})\b", text)
    if digits:
        return f"ISO {digits.group(1)}"
    return text


# ── Row scope extraction ─────────────────────────────────────────────────

_SCOPE_CANDIDATE_KEYS = ("source_standard", "standard", "scope", "norma")


def extract_row_scope(item: dict[str, Any]) -> str:
    """Extract the scope label from any known nesting pattern.

    Searches ``metadata.source_standard``, ``metadata.standard``,
    ``metadata.scope``, ``metadata.norma``, and top-level
    ``source_standard``.  Returns upper-cased label or ``""``.
    """
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
    """If exactly one clause ref appears near *standard* in *query*, return it.

    Uses a text window of [-80, +120] chars around the standard mention.
    Returns ``None`` when zero or multiple clauses are found.
    """
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
    """Extract the ordered, deduplicated tuple of requested standards
    from a ``scope_context`` dict (which may nest a ``filters`` sub-dict).
    """
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


# ── Scope penalty helpers ────────────────────────────────────────────────


def apply_scope_penalty(
    results: list[dict[str, Any]],
    requested_scopes: tuple[str, ...],
    *,
    penalty_factor: float = 0.75,
) -> list[dict[str, Any]]:
    """Down-weight items whose scope doesn't match any *requested_scopes*.

    Items from a non-matching scope have their score multiplied by
    ``(1 - penalty_factor)``.  The original item is **not** mutated;
    a shallow copy is created for penalised rows.
    """
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
