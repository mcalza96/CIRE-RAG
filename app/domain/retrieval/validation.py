from __future__ import annotations

from typing import Any
from app.api.v1.schemas.retrieval_advanced import ScopeIssue

_ALLOWED_FILTER_KEYS = {"metadata", "time_range", "source_standard", "source_standards"}
_RESERVED_METADATA_KEYS = {"tenant_id", "institution_id"}

def validate_metadata_values(metadata: Any) -> tuple[dict[str, Any], list[ScopeIssue]]:
    """Validate and normalize metadata filters."""
    if metadata is None:
        return {}, []
    if not isinstance(metadata, dict):
        return {}, [
            ScopeIssue(
                code="INVALID_METADATA_FILTER",
                field="filters.metadata",
                message="metadata must be a dictionary",
            )
        ]
    
    normalized: dict[str, Any] = {}
    violations: list[ScopeIssue] = []
    
    _SCALAR_TYPES = (str, int, float, bool)
    _RESERVED_METADATA_KEYS = {"tenant_id", "institution_id"}

    for key, value in metadata.items():
        if key in _RESERVED_METADATA_KEYS:
            violations.append(
                ScopeIssue(
                    code="RESERVED_METADATA_KEY",
                    field=f"filters.metadata.{key}",
                    message="this key is reserved for internal use",
                )
            )
            continue
            
        if value is None:
            continue
            
        if isinstance(value, _SCALAR_TYPES):
            normalized[key] = value
        elif isinstance(value, list) and all(isinstance(v, _SCALAR_TYPES) for v in value):
            normalized[key] = value
        else:
            violations.append(
                ScopeIssue(
                    code="INVALID_METADATA_VALUE",
                    field=f"filters.metadata.{key}",
                    message="metadata values must be scalars or lists of scalars",
                )
            )
            
    return normalized, violations

def validate_time_range(time_range: Any) -> tuple[dict[str, Any], list[ScopeIssue]]:
    """Validate and normalize time_range filters."""
    if time_range is None:
        return {}, []
    if not isinstance(time_range, dict):
        return {}, [
            ScopeIssue(
                code="INVALID_TIME_RANGE",
                field="filters.time_range",
                message="time_range must be a dictionary",
            )
        ]
    
    # We expect 'from' and 'to' as keys (ISO strings)
    # We just validate they can be coerced or are valid
    normalized: dict[str, Any] = {}
    violations: list[ScopeIssue] = []
    
    for key in ("from", "to"):
        val = time_range.get(key)
        if val is None:
            continue
        if not isinstance(val, str):
             violations.append(
                 ScopeIssue(
                     code="INVALID_TIME_VALUE",
                     field=f"filters.time_range.{key}",
                     message="time value must be a string",
                 )
             )
        else:
            normalized[key] = val
            
    return normalized, violations

def validate_source_standards(
    raw_filters: dict[str, Any], 
) -> tuple[str | None, list[str], list[ScopeIssue]]:
    """Validate and normalize source standards."""
    violations: list[ScopeIssue] = []
    source_standard = str(raw_filters.get("source_standard") or "").strip() or None
    source_standards_raw = raw_filters.get("source_standards")
    source_standards: list[str] = []
    
    if source_standards_raw is not None:
        if not isinstance(source_standards_raw, list):
            violations.append(
                ScopeIssue(
                    code="INVALID_SCOPE_FILTER",
                    field="filters.source_standards",
                    message="source_standards must be a list of strings",
                )
            )
        else:
            for value in source_standards_raw:
                if not isinstance(value, str) or not value.strip():
                    violations.append(
                        ScopeIssue(
                            code="INVALID_SCOPE_FILTER",
                            field="filters.source_standards",
                            message="source_standards entries must be non-empty strings",
                        )
                    )
                    continue
                source_standards.append(value.strip())
            source_standards = list(dict.fromkeys(source_standards))

    if source_standard and source_standard not in source_standards:
        source_standards.insert(0, source_standard)
    
    if not source_standard and source_standards:
        source_standard = source_standards[0]

    if len(source_standards) > 1:
        source_standard = None
    elif len(source_standards) == 1:
        source_standard = source_standards[0]
        source_standards = []
        
    return source_standard, source_standards, violations

def validate_retrieval_filters(
    raw_filters: dict[str, Any]
) -> tuple[dict[str, Any], list[ScopeIssue]]:
    """Validate and normalize all retrieval filters."""
    violations: list[ScopeIssue] = []
    
    unknown_keys = sorted(set(raw_filters.keys()) - _ALLOWED_FILTER_KEYS)
    for key in unknown_keys:
        violations.append(
            ScopeIssue(
                code="INVALID_SCOPE_FILTER",
                field=f"filters.{key}",
                message="filter key is not allowed",
            )
        )

    metadata_norm, metadata_violations = validate_metadata_values(
        raw_filters.get("metadata")
    )
    violations.extend(metadata_violations)
    
    time_range_norm, time_range_violations = validate_time_range(
        raw_filters.get("time_range")
    )
    violations.extend(time_range_violations)

    source_standard, source_standards, standard_violations = validate_source_standards(raw_filters)
    violations.extend(standard_violations)

    normalized = {
        "metadata": metadata_norm or None,
        "time_range": time_range_norm,
        "source_standard": source_standard,
        "source_standards": source_standards or None,
    }
    
    return normalized, violations

def matches_time_range(row: dict[str, Any], time_range: dict[str, Any] | None) -> bool | None:
    """Check if a row matches a time range filter."""
    if not time_range:
        return None
    field = str(time_range.get("field") or "").strip()
    if field not in {"created_at", "updated_at"}:
        return False
    row_value = row.get(field)
    if not isinstance(row_value, str) or not row_value.strip():
        return False
    try:
        from datetime import datetime, timezone
        dt_row = datetime.fromisoformat(row_value.replace("Z", "+00:00"))
        if dt_row.tzinfo is None:
            dt_row = dt_row.replace(tzinfo=timezone.utc)
        
        lower_raw = time_range.get("from")
        if lower_raw:
            dt_lower = datetime.fromisoformat(lower_raw.replace("Z", "+00:00"))
            if dt_lower.tzinfo is None:
                dt_lower = dt_lower.replace(tzinfo=timezone.utc)
            if dt_row < dt_lower:
                return False
                
        upper_raw = time_range.get("to")
        if upper_raw:
            dt_upper = datetime.fromisoformat(upper_raw.replace("Z", "+00:00"))
            if dt_upper.tzinfo is None:
                dt_upper = dt_upper.replace(tzinfo=timezone.utc)
            if dt_row > dt_upper:
                return False
        return True
    except Exception:
        return False

def metadata_keys_matched(
    row: dict[str, Any], metadata_filter: dict[str, Any] | None
) -> list[str]:
    """Identify which metadata keys from the filter are present and matching in the row."""
    if not metadata_filter:
        return []
    row_meta_raw = row.get("metadata")
    row_meta = row_meta_raw if isinstance(row_meta_raw, dict) else {}
    matched: list[str] = []
    
    for key, val in metadata_filter.items():
        if key in row:
             actual = row[key]
        elif key in row_meta:
             actual = row_meta[key]
        else:
             continue
             
        if isinstance(val, list):
             if actual in val:
                 matched.append(key)
        elif actual == val:
             matched.append(key)
    return sorted(matched)
