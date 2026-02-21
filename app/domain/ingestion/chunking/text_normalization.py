"""Text processing helpers for Visual Anchor token injection."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, List, Optional
from uuid import UUID, uuid4


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


def normalize_embedding(raw_embedding: Any) -> List[float]:
    """Ensures embedding is a list of floats, handling many raw formats."""
    if isinstance(raw_embedding, list):
        out: List[float] = []
        for value in raw_embedding:
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out

    if isinstance(raw_embedding, str):
        text = raw_embedding.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            out: List[float] = []
            for value in parsed:
                try:
                    out.append(float(value))
                except (TypeError, ValueError):
                    continue
            return out
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1]
            out: List[float] = []
            for token in inner.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    out.append(float(token))
                except (TypeError, ValueError):
                    continue
            return out

    return []


def ensure_chunk_ids(chunks: List[Any]) -> None:
    """Modifica la lista de chunks in-place para asegurar que cada uno tenga un ID (UUID)."""
    for chunk in chunks:
        cid = chunk.get("id") if isinstance(chunk, dict) else getattr(chunk, "id", None)
        if not cid:
            new_id = str(uuid4())
            if isinstance(chunk, dict):
                chunk["id"] = new_id
            else:
                setattr(chunk, "id", new_id)
