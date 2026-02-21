import re
from typing import Any, Dict, Tuple
from app.domain.retrieval.ports import IScopeResolverPolicy

class ISOScopeResolverPolicy(IScopeResolverPolicy):
    """
    ISO-specific implementation of scope resolution.
    Encapsulates knowledge about ISO 9001, 14001, etc.
    """

    def extract_requested_scopes(self, query: str) -> Tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        # Matches patterns like ISO 9001, ISO-14001, iso:45001
        for match in re.findall(r"\biso\s*[-:]?\s*(\d{4,5})\b", (query or ""), flags=re.IGNORECASE):
            value = f"ISO {match}"
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)

    def has_ambiguous_reference(self, query: str) -> bool:
        # ISO clauses usually look like "4.4.1" or "9.2"
        return bool(re.search(r"\b\d+(?:\.\d+)+\b", (query or "")))

    def suggest_scope_candidates(self, query: str) -> Tuple[str, ...]:
        text = (query or "").strip().lower()
        hints: dict[str, tuple[str, ...]] = {
            "ISO 9001": ("calidad", "cliente", "producto", "servicio"),
            "ISO 14001": ("ambient", "legal", "cumplimiento", "aspecto ambiental"),
            "ISO 45001": ("seguridad", "salud", "sst", "trabajador"),
        }
        ranked = [standard for standard, keys in hints.items() if any(key in text for key in keys)]
        if ranked:
            return tuple(dict.fromkeys(ranked))
        
        # Default fallback for ISO world
        return ("ISO 9001", "ISO 14001", "ISO 45001")

    def extract_item_scope(self, item: Dict[str, Any]) -> str:
        meta_raw = item.get("metadata")
        metadata: Dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
        
        # Check various metadata fields where scope might live
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
                
        # Content-based heuristic fallback
        content = str(item.get("content") or "").upper()
        for token in ("ISO 9001", "ISO 14001", "ISO 45001"):
            if token in content:
                return token
        return ""
