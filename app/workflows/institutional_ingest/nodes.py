import os
from typing import Any

import structlog
from app.application.services.institutional_ingestion_pipeline_service import (
    InstitutionalIngestionPipelineService,
)
from app.domain.types.ingestion_status import IngestionStatus
from app.workflows.institutional_ingest.state import InstitutionalState

logger = structlog.get_logger(__name__)


pipeline_service = InstitutionalIngestionPipelineService()


def _classify_structural_content(content: str) -> dict[str, Any]:
    text = str(content or "")
    lowered = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    short_lines = [line for line in lines[:120] if len(line) <= 120]
    dot_leader_lines = sum(1 for line in short_lines if "..." in line or " ." in line)
    indexed_lines = sum(
        1
        for line in short_lines
        if any(char.isdigit() for char in line[-8:]) and len(line.split()) <= 16
    )
    toc_markers = (
        "table of contents",
        "indice",
        "indice general",
        "contenido",
        "contents",
    )
    frontmatter_markers = (
        "revision",
        "version",
        "aprobado por",
        "control de cambios",
        "vigencia",
    )
    has_toc_keyword = any(marker in lowered for marker in toc_markers)
    frontmatter_hits = sum(1 for marker in frontmatter_markers if marker in lowered)
    is_toc = bool(has_toc_keyword or dot_leader_lines >= 3 or indexed_lines >= 8)
    is_frontmatter = bool(frontmatter_hits >= 2 and len(text) <= 3500)
    route = "structural" if (is_toc or is_frontmatter) else "semantic"
    return {
        "is_toc": is_toc,
        "is_frontmatter": is_frontmatter,
        "route": route,
        "signals": {
            "dot_leader_lines": dot_leader_lines,
            "indexed_lines": indexed_lines,
            "frontmatter_hits": frontmatter_hits,
            "has_toc_keyword": has_toc_keyword,
        },
    }


async def classify_content_node(state: InstitutionalState):
    parsed_content = str(state.get("parsed_content") or "").strip()
    if not parsed_content:
        return {
            "content_route": "semantic",
            "content_classification": {"route": "semantic", "reason": "empty_content"},
        }

    classification = _classify_structural_content(parsed_content)
    return {
        "content_route": classification.get("route", "semantic"),
        "content_classification": classification,
    }


def route_content_node(state: InstitutionalState) -> str:
    route = str(state.get("content_route") or "semantic").strip().lower()
    return "process_structural_graph" if route == "structural" else "embed"


async def ingest_node(state: InstitutionalState):
    logger.info("ingest_node_start")
    file_path = state.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return {
            "status": IngestionStatus.FAILED.value,
            "error": f"File not found: {file_path}",
        }
    return await pipeline_service.ingest_file(file_path)


async def parse_node(state: InstitutionalState):
    logger.info("parse_node_start")
    return await pipeline_service.parse_raw_text(str(state.get("raw_text") or ""))


async def embed_node(state: InstitutionalState):
    logger.info("embed_node_start")
    return await pipeline_service.embed_content(str(state.get("parsed_content") or ""))


async def process_structural_graph_node(state: InstitutionalState):
    classification = state.get("content_classification")
    return {
        "semantic_chunks": [],
        "content_route": "structural",
        "content_classification": classification,
    }


async def index_node(state: InstitutionalState):
    logger.info("index_node_start", security_critical=True)
    try:
        return await pipeline_service.index_chunks(
            tenant_id=state.get("tenant_id"),
            document_id=state.get("document_id"),
            chunks_data=state.get("semantic_chunks") or [],
        )
    except Exception as exc:
        return {"status": IngestionStatus.FAILED.value, "error": f"Indexing failed: {str(exc)}"}
