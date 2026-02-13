import re
from typing import Dict, Any, Tuple
import structlog

logger = structlog.get_logger(__name__)

PATTERNS = {
    "exercise_id": re.compile(r"(?:Ejercicio|Problema|Actividad)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "theorem_id": re.compile(r"(?:Teorema|Lema|Corolario|Proposición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "definition_id": re.compile(r"(?:Definición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "example_id": re.compile(r"(?:Ejemplo)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "section_id": re.compile(r"(?:Sección)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "figure_id": re.compile(r"(?:Figura|Imagen|Gráfico)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
}

ISO_PATTERN = re.compile(r"\bISO\s*[-:]?\s*(\d{4,5})(?::\s*\d{4})?\b", re.IGNORECASE)
CLAUSE_PATTERN = re.compile(r"\b\d+(?:\.\d+)+\b")


def enrich_metadata(text: str, current_metadata: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Scan text patterns and enrich metadata deterministically."""
    updates: Dict[str, Any] = {}
    found_tags = []

    for key, pattern in PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1)
            updates[key] = value
            found_tags.append(f"[{key.upper()}: {value}]")

    iso_match = ISO_PATTERN.search(text)
    if iso_match:
        standard = f"ISO {iso_match.group(1)}"
        updates["source_standard"] = standard
        updates["scope"] = standard

    clause_refs = list(dict.fromkeys(CLAUSE_PATTERN.findall(text)))
    if clause_refs:
        updates["clause_refs"] = clause_refs[:20]

    new_metadata = current_metadata.copy()
    new_metadata.update(updates)

    prefix = " ".join(found_tags)
    if prefix and not text.startswith("["):
        return f"{prefix}\n{text}", new_metadata

    return text, new_metadata


class MetadataEnricher:
    """Compatibility wrapper around enrich_metadata."""

    def enrich(self, text: str, current_metadata: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        return enrich_metadata(text, current_metadata)
