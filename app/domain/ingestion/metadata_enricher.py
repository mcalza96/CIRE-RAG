import re
from typing import Dict, Any, Tuple
import structlog

logger = structlog.get_logger(__name__)

PATTERNS = {
    "exercise_id": re.compile(r"(?:Ejercicio|Problema|Actividad)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "theorem_id": re.compile(
        r"(?:Teorema|Lema|Corolario|Proposición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE
    ),
    "definition_id": re.compile(r"(?:Definición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "example_id": re.compile(r"(?:Ejemplo)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "section_id": re.compile(r"(?:Sección)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "figure_id": re.compile(r"(?:Figura|Imagen|Gráfico)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    "clause_id": re.compile(r"\b(?:cl[aá]usula\s*)?(\d+(?:\.\d+)+)\b", re.IGNORECASE),
}

ISO_PATTERN = re.compile(r"\bISO\s*[-:]?\s*(\d{4,5})(?::\s*\d{4})?\b", re.IGNORECASE)
CLAUSE_PATTERN = re.compile(r"\b\d+(?:\.\d+)+\b")
CLAUSE_TITLE_PATTERN = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+)+)\s*[\)\.:\-]?\s*([^\n]{4,140})",
    re.IGNORECASE,
)


def _canonical_standard(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    match = re.search(r"\b(?:ISO\s*[-:]?\s*)?(\d{4,5})\b", value, flags=re.IGNORECASE)
    if not match:
        return value.upper()
    return f"ISO {match.group(1)}"


def enrich_metadata(
    text: str,
    current_metadata: Dict[str, Any],
    *,
    allow_clause_extraction: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """Scan text patterns and enrich metadata deterministically."""
    updates: Dict[str, Any] = {}
    found_tags = []

    for key, pattern in PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1)
            updates[key] = value
            found_tags.append(f"[{key.upper()}: {value}]")

    iso_matches = [f"ISO {m}" for m in ISO_PATTERN.findall(text)]
    standards = list(dict.fromkeys(_canonical_standard(s) for s in iso_matches if s))
    if standards:
        updates["source_standard"] = standards[0]
        updates["scope"] = standards[0]
        if len(standards) > 1:
            updates["source_standards"] = standards

    if allow_clause_extraction:
        clause_refs = list(dict.fromkeys(CLAUSE_PATTERN.findall(text)))
        if clause_refs:
            updates["clause_refs"] = clause_refs[:20]
            updates.setdefault("clause_id", clause_refs[0])

        clause_title_match = CLAUSE_TITLE_PATTERN.search(text)
        if clause_title_match:
            updates["clause_anchor"] = clause_title_match.group(1)
            updates["clause_title"] = clause_title_match.group(2).strip()
            updates.setdefault("clause_id", clause_title_match.group(1))

    new_metadata = current_metadata.copy()
    new_metadata.update(updates)

    prefix = " ".join(found_tags)
    if prefix and not text.startswith("["):
        return f"{prefix}\n{text}", new_metadata

    return text, new_metadata


class MetadataEnricher:
    """Compatibility wrapper around enrich_metadata."""

    def enrich(
        self,
        text: str,
        current_metadata: Dict[str, Any],
        *,
        allow_clause_extraction: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        return enrich_metadata(
            text,
            current_metadata,
            allow_clause_extraction=allow_clause_extraction,
        )
