import re
import json
from typing import Any, Dict, Tuple
from app.domain.retrieval.ports import IScopeResolverPolicy


DEFAULT_SCOPE_EXTRACTION_REGEX = r"\bISO\s*[-:]?\s*\d{4,5}\b"
DEFAULT_SCOPE_AMBIGUITY_REGEX = (
    r"\b(?:cl(?:a|á)usula|clause|numeral|apartado|secci[oó]n)\s*\d+(?:\.\d+)*\b"
)


class GeneralScopeResolverPolicy(IScopeResolverPolicy):
    """
    Agnostic implementation of scope resolution.
    Driven by regex patterns and keyword maps from settings.
    """

    def __init__(
        self,
        *,
        scope_extraction_regex: str,
        scope_ambiguity_regex: str,
        scope_keyword_map_json: str = "{}",
    ) -> None:
        self._scope_extraction_regex = (
            str(scope_extraction_regex or "").strip() or DEFAULT_SCOPE_EXTRACTION_REGEX
        )
        self._scope_ambiguity_regex = (
            str(scope_ambiguity_regex or "").strip() or DEFAULT_SCOPE_AMBIGUITY_REGEX
        )
        try:
            raw_hints = json.loads(scope_keyword_map_json or "{}")
            self._hints: Dict[str, list[str]] = raw_hints if isinstance(raw_hints, dict) else {}
        except Exception:
            self._hints = {}

    def extract_requested_scopes(self, query: str) -> Tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []

        for match in re.findall(self._scope_extraction_regex, (query or ""), flags=re.IGNORECASE):
            # Normalize match (e.g. UPPER)
            value = str(match).strip().upper()
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)

    def has_ambiguous_reference(self, query: str) -> bool:
        """Detects references (like clause IDs) that might require scope disambiguation."""
        return bool(re.search(self._scope_ambiguity_regex, (query or "")))

    def suggest_scope_candidates(self, query: str) -> Tuple[str, ...]:
        """Suggests potential scopes based on keyword mapping."""
        text = (query or "").strip().lower()

        if not self._hints:
            return tuple()

        ranked = [
            scope
            for scope, keys in self._hints.items()
            if any(str(key).lower() in text for key in keys)
        ]
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
