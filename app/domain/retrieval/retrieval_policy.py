from __future__ import annotations

import re
from typing import Any


_WS_RE = re.compile(r"\s+")
_TABLE_BORDER_RE = re.compile(r"^[\s|:+\-]{4,}$")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TOC_DOT_LEADER_LINE_RE = re.compile(r"^\s*.+\.{3,}\s*\d+\s*$", re.MULTILINE)
_TOC_KEYWORD_RE = re.compile(r"\b(table of contents|contents|indice|índice|contenido)\b")


def _metadata_dict(row: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = row.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    nested_row = metadata.get("row")
    if not isinstance(nested_row, dict):
        return metadata
    nested_metadata = nested_row.get("metadata")
    if not isinstance(nested_metadata, dict):
        return metadata
    merged = dict(nested_metadata)
    merged.update(metadata)
    return merged


def _is_structural_only_row(row: dict[str, Any]) -> bool:
    metadata = _metadata_dict(row)
    retrieval_eligible = metadata.get("retrieval_eligible")
    if retrieval_eligible is False:
        return True
    if metadata.get("is_toc") is True:
        return True
    if metadata.get("is_frontmatter") is True:
        return True

    content = str(row.get("content") or "")
    lowered = content.lower()
    dot_leaders = len(_TOC_DOT_LEADER_LINE_RE.findall(content))
    if dot_leaders >= 2:
        return True
    if dot_leaders >= 1 and _TOC_KEYWORD_RE.search(lowered):
        return True
    return False


def apply_search_hints(
    query: str, hints: list[dict[str, Any]] | None
) -> tuple[str, dict[str, Any]]:
    text = str(query or "").strip()
    if not text or not hints:
        return text, {"applied": False, "applied_hints": [], "expanded_terms": []}

    lower_text = text.lower()
    expanded_terms: list[str] = []
    applied: list[dict[str, Any]] = []

    for hint in hints:
        if not isinstance(hint, dict):
            continue
        term = str(hint.get("term") or "").strip()
        if not term:
            continue
        if term.lower() not in lower_text:
            continue
        raw_expand = hint.get("expand_to")
        expands = raw_expand if isinstance(raw_expand, list) else []
        additions: list[str] = []
        for item in expands:
            value = str(item or "").strip()
            if not value:
                continue
            value_lower = value.lower()
            if value_lower in lower_text or value in expanded_terms:
                continue
            additions.append(value)
        if additions:
            expanded_terms.extend(additions)
            applied.append({"term": term, "expand_to": additions})

    if not expanded_terms:
        return text, {"applied": False, "applied_hints": [], "expanded_terms": []}

    expanded_query = f"{text} {' '.join(expanded_terms)}".strip()
    return expanded_query, {
        "applied": True,
        "applied_hints": applied,
        "expanded_terms": expanded_terms,
    }


def score_space_from_item(item: dict[str, Any]) -> str:
    score_space = str(item.get("score_space") or "").strip().lower()
    if score_space:
        return score_space
    metadata_raw = item.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    return str(metadata.get("score_space") or "").strip().lower()


def filter_rows_by_min_score(
    rows: list[dict[str, Any]],
    *,
    min_score: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if min_score is None:
        return rows, {"applied": False, "reason": "no_min_score"}

    threshold = float(min_score)
    kept: list[dict[str, Any]] = []
    dropped = 0
    bypassed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        score_space = score_space_from_item(row)
        if score_space in {"rrf", "mixed"}:
            kept.append(row)
            bypassed += 1
            continue
        raw_score = row.get("similarity")
        if raw_score is None:
            raw_score = row.get("score")
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            dropped += 1
            continue
        if score >= threshold:
            kept.append(row)
        else:
            dropped += 1

    return kept, {
        "applied": True,
        "threshold": threshold,
        "kept": len(kept),
        "dropped": dropped,
        "score_space_bypassed": bypassed,
    }


def _clean_content(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _TABLE_BORDER_RE.fullmatch(line):
            continue
        line = _MARKDOWN_LINK_RE.sub(r"\1", line)
        line = _WS_RE.sub(" ", line).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def reduce_structural_noise_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    changed = 0
    dropped = 0
    dropped_structural = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _is_structural_only_row(row):
            dropped += 1
            dropped_structural += 1
            continue
        current = str(row.get("content") or "")
        cleaned = _clean_content(current)
        if not cleaned:
            dropped += 1
            continue
        next_row = dict(row)
        if cleaned != current.strip():
            next_row["content"] = cleaned
            changed += 1
        cleaned_rows.append(next_row)

    return cleaned_rows, {
        "applied": True,
        "changed": changed,
        "dropped": dropped,
        "dropped_structural": dropped_structural,
        "kept": len(cleaned_rows),
    }
