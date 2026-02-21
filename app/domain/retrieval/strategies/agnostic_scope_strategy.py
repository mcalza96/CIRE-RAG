import re
import json
from typing import Any, Dict, Tuple
from app.domain.retrieval.ports import IScopeResolverPolicy
from app.infrastructure.settings import settings

class GeneralScopeResolverPolicy(IScopeResolverPolicy):
    """
    Agnostic implementation of scope resolution.
    Driven by regex patterns and keyword maps from settings.
    """

    def extract_requested_scopes(self, query: str) -> Tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        
        pattern = settings.SCOPE_EXTRACTION_REGEX
        if not pattern:
            return tuple()

        for match in re.findall(pattern, (query or ""), flags=re.IGNORECASE):
            # Normalize match (e.g. UPPER)
            value = str(match).strip().upper()
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)

    def has_ambiguous_reference(self, query: str) -> bool:
        """Detects references (like clause IDs) that might require scope disambiguation."""
        pattern = settings.SCOPE_AMBIGUITY_REGEX
        if not pattern:
            return False
        return bool(re.search(pattern, (query or "")))

    def suggest_scope_candidates(self, query: str) -> Tuple[str, ...]:
        """Suggests potential scopes based on keyword mapping."""
        text = (query or "").strip().lower()
        
        try:
            hints: Dict[str, list[str]] = json.loads(settings.SCOPE_KEYWORD_MAP or "{}")
        except Exception:
            hints = {}

        if not hints:
            return tuple()

        ranked = [scope for scope, keys in hints.items() if any(str(key).lower() in text for key in keys)]
        return tuple(dict.fromkeys(ranked))

    def extract_item_scope(self, item: Dict[str, Any]) -> str:
        """Extracts scope identifier from a retrieval item's metadata."""
        meta_raw = item.get("metadata")
        metadata: Dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
        
        # Check generalized metadata fields
        candidates = [
            metadata.get("source_standard"),
            metadata.get("standard"),
            metadata.get("scope"),
            metadata.get("category"),
            item.get("source_standard"),
        ]
        
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
                
        return ""
