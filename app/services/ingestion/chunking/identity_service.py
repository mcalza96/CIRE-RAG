import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.schemas.ingestion_schemas import IngestionMetadata


_ISO_DOC_PATTERN = re.compile(r"\bISO\s*[-:_]?\s*(\d{4,5})\b", re.IGNORECASE)
_NOM_ISO_DOC_PATTERN = re.compile(r"\bNOM\s*[-_ ]?ISO\s*[-_ ]?(\d{4,5})\b", re.IGNORECASE)


class ChunkIdentityService:
    @staticmethod
    def resolve_section_node_id(source_id: Any, structure_context: dict[str, Any]) -> str | None:
        if not isinstance(structure_context, dict):
            return None
        section_ref = str(structure_context.get("section_ref") or "").strip()
        if not section_ref:
            return None
        source_text = str(source_id or "").strip()
        if not source_text:
            return None
        return str(uuid5(NAMESPACE_URL, f"doc-structure:{source_text}:{section_ref}"))

    @staticmethod
    def infer_document_standards(metadata: IngestionMetadata) -> list[str]:
        candidates: list[str] = []
        nested = metadata.metadata if isinstance(metadata.metadata, dict) else {}
        for key in ("source_standard", "standard", "scope"):
            raw = nested.get(key)
            if isinstance(raw, str) and raw.strip():
                candidates.append(raw.strip())
        raw_many = nested.get("source_standards")
        if isinstance(raw_many, list):
            for item in raw_many:
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())

        for text in (
            metadata.title,
            str(nested.get("filename") or ""),
            str(nested.get("storage_path") or ""),
        ):
            if not text:
                continue
            for match in _ISO_DOC_PATTERN.findall(text):
                candidates.append(f"ISO {match}")
            for match in _NOM_ISO_DOC_PATTERN.findall(text):
                candidates.append(f"ISO {match}")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            match = re.search(r"\b(?:ISO\s*[-:_]?\s*)?(\d{4,5})\b", item, flags=re.IGNORECASE)
            canon = f"ISO {match.group(1)}" if match else item.strip().upper()
            if not canon or canon in seen:
                continue
            seen.add(canon)
            normalized.append(canon)
        return normalized
