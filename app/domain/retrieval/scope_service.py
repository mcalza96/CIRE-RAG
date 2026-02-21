from __future__ import annotations
import copy
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional, Tuple

from app.domain.retrieval.scope_utils import (
    clause_near_standard,
    extract_clause_refs,
    extract_requested_standards,
    extract_row_scope,
    requested_scopes_from_context,
    scope_penalty_ratio,
)

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
