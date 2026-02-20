"""Text processing helpers for Visual Anchor token injection."""

from __future__ import annotations

import re
from typing import Any, Mapping
from uuid import UUID


def inject_anchor_token(text: str, context: Mapping[str, Any] | None, node_id: UUID | str) -> str:
    """Inject a regex-friendly visual anchor token into chunk text.

    Token format:
    <<VISUAL_ANCHOR: {uuid} | TYPE: {type} | DESC: {short_summary}>>
    """

    normalized_text = text or ""
    context_data = dict(context or {})
    visual_type = _sanitize_label(str(context_data.get("type", "table")), fallback="table")
    short_summary = _sanitize_desc(str(context_data.get("short_summary", "Visual node")))
    node_id_str = str(node_id)

    token = f"<<VISUAL_ANCHOR: {node_id_str} | TYPE: {visual_type} | DESC: {short_summary}>>"
    if node_id_str in normalized_text:
        return normalized_text

    placeholder = context_data.get("placeholder")
    if isinstance(placeholder, str) and placeholder and placeholder in normalized_text:
        return normalized_text.replace(placeholder, token, 1)

    anchor_after = context_data.get("anchor_after")
    if isinstance(anchor_after, str) and anchor_after:
        idx = normalized_text.find(anchor_after)
        if idx >= 0:
            insert_at = idx + len(anchor_after)
            return normalized_text[:insert_at] + "\n" + token + normalized_text[insert_at:]

    if normalized_text.endswith("\n"):
        return normalized_text + token
    if normalized_text:
        return normalized_text + "\n\n" + token
    return token


def _sanitize_desc(value: str) -> str:
    """Normalize summary text so anchor token remains single-line and parseable."""

    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = cleaned.replace("|", "/").replace(">>", "]").replace("<<", "[")
    if not cleaned:
        cleaned = "Visual node"
    return cleaned[:180]


def _sanitize_label(value: str, fallback: str) -> str:
    """Normalize short label values for token metadata."""

    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", value.strip().lower())
    return cleaned or fallback
