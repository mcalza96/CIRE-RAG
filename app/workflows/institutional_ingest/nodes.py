"""
Nodes for the Institutional Ingest Graph.
Implements Ingest -> Parse -> Embed -> Index pipeline with Strict Tenant Isolation.
"""

import asyncio
import os
import fitz  # PyMuPDF
from dotenv import load_dotenv
from app.core.llm import get_llm
from app.core.settings import settings

# Initialize Models
from app.services.embedding_service import JinaEmbeddingService
from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository
from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.domain.types.ingestion_status import IngestionStatus
from app.domain.schemas import ContentChunk
from langchain_core.messages import SystemMessage, HumanMessage
from app.workflows.institutional_ingest.state import InstitutionalState
from app.core.prompts.institutional import InstitutionalPrompts


class SecurityContextError(Exception):
    """Raised when indexing is attempted without proper tenant context."""

    pass


# Initialize Models
chunker = JinaEmbeddingService.get_instance()
PARSE_WINDOW_CONCURRENCY = max(1, min(10, int(getattr(settings, "PARSER_WINDOW_CONCURRENCY", 5))))
PARSE_WINDOW_MAX_RETRIES = max(0, int(getattr(settings, "PARSER_WINDOW_MAX_RETRIES", 2)))
PARSE_WINDOW_RETRY_BASE_DELAY_SECONDS = max(
    0.1, float(getattr(settings, "PARSER_WINDOW_RETRY_BASE_DELAY_SECONDS", 0.75))
)


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


def _extract_status_code(exc: Exception) -> int | None:
    for attr_name in ("status_code", "status", "http_status"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return int(code) if isinstance(code, int) else None


def _is_retryable_parse_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    message = str(exc).lower()
    retryable_markers = (
        "too many requests",
        "rate limit",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
    )
    return any(marker in message for marker in retryable_markers)


# --- NODES ---


async def ingest_node(state: InstitutionalState):
    """
    Reads the PDF file and extracts raw text.
    """
    print("--- INGEST NODE ---")
    file_path = state.get("file_path")

    if not file_path or not os.path.exists(file_path):
        return {"status": IngestionStatus.FAILED.value, "error": f"File not found: {file_path}"}

    try:
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        doc.close()

        return {"raw_text": full_text}
    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Ingestion failed: {str(e)}"}


async def parse_node(state: InstitutionalState):
    """
    Uses LLM to clean and structure the raw text into Markdown.
    Implements map-reduce for large documents to avoid content loss.
    """
    print("--- PARSE NODE ---")
    raw_text = state.get("raw_text", "")

    if not raw_text:
        return {"status": IngestionStatus.FAILED.value, "error": "No raw text to parse"}

    system_prompt = InstitutionalPrompts.PARSING_SYSTEM

    # Map-reduce for large documents
    WINDOW_SIZE = 25000
    OVERLAP = 2000

    if len(raw_text) <= WINDOW_SIZE:
        # Small document: single pass
        windows = [raw_text]
    else:
        # Large document: sliding windows with overlap
        windows = []
        start = 0
        while start < len(raw_text):
            end = min(start + WINDOW_SIZE, len(raw_text))
            windows.append(raw_text[start:end])
            start += WINDOW_SIZE - OVERLAP
        print(f"    Map-reduce: {len(windows)} windows for {len(raw_text)} chars")

    try:
        semaphore = asyncio.Semaphore(PARSE_WINDOW_CONCURRENCY)
        llm = get_llm(temperature=0)

        async def _process_window(i: int, window: str) -> str:
            user_content = f"CONTENIDO A ESTRUCTURAR (parte {i + 1}/{len(windows)}):\n\n{window}"
            total_attempts = PARSE_WINDOW_MAX_RETRIES + 1
            for attempt in range(1, total_attempts + 1):
                try:
                    # Parallel parsing with bounded concurrency to avoid rate/memory spikes.
                    async with semaphore:
                        response = await llm.ainvoke(
                            [
                                SystemMessage(content=system_prompt),
                                HumanMessage(content=user_content),
                            ]
                        )
                    return str(response.content)
                except Exception as exc:
                    should_retry = _is_retryable_parse_error(exc) and attempt < total_attempts
                    if not should_retry:
                        raise RuntimeError(
                            f"Window parsing failed at part {i + 1}/{len(windows)}: {str(exc)}"
                        ) from exc

                    backoff_seconds = PARSE_WINDOW_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    print(
                        f"    Retry parte {i + 1}/{len(windows)} intento {attempt + 1}/{total_attempts} "
                        f"en {backoff_seconds:.2f}s"
                    )
                    await asyncio.sleep(backoff_seconds)

            raise RuntimeError(
                f"Window parsing failed at part {i + 1}/{len(windows)}: max retries exceeded"
            )

        tasks = [_process_window(i, window) for i, window in enumerate(windows)]
        # gather preserves order of input tasks, so document coherence is retained.
        parsed_parts = await asyncio.gather(*tasks)

        # Reduce: concatenate all parsed parts
        full_parsed = "\n\n".join(parsed_parts)
        return {"parsed_content": full_parsed}
    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Parsing failed: {str(e)}"}


async def embed_node(state: InstitutionalState):
    """
    Embeds the parsed markdown using Jina Late Chunking.
    """
    print("--- EMBED NODE ---")
    content = state.get("parsed_content", "")

    if not content:
        return {"status": IngestionStatus.FAILED.value, "error": "No parsed content to embed"}

    try:
        # Jina handles chunking internally (Facade handles lazy-load)
        chunks = await chunker.chunk_and_encode(content)

        # Format for state
        semantic_chunks = []
        for c in chunks:
            semantic_chunks.append(
                {
                    "content": c["content"],
                    "embedding": c["embedding"],
                    "metadata": {"char_start": c["char_start"], "char_end": c["char_end"]},
                }
            )

        return {"semantic_chunks": semantic_chunks}
    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Embedding failed: {str(e)}"}


async def process_structural_graph_node(state: InstitutionalState):
    classification = state.get("content_classification")
    return {
        "semantic_chunks": [],
        "content_route": "structural",
        "content_classification": classification,
    }


async def index_node(state: InstitutionalState):
    """
    Pushes vectors to Supabase using Repository Pattern (STRICT SECURITY).
    """
    print("--- INDEX NODE (SECURITY CRITICAL) ---")

    tenant_id = state.get("tenant_id")
    document_id = state.get("document_id")
    chunks_data = state.get("semantic_chunks", [])

    # 1. SECURITY CHECK
    if not tenant_id:
        print("!!! SECURITY ALERT: Attempted indexing without Tenant ID !!!")
        raise SecurityContextError("Operation blocked: Missing tenant_id in secure context.")

    if not chunks_data:
        return {"status": IngestionStatus.SUCCESS.value, "indexed_count": 0}

    try:
        repo = SupabaseContentRepository()

        # Map to Domain Entities
        domain_chunks = []
        for i, c in enumerate(chunks_data):
            domain_chunks.append(
                ContentChunk(
                    source_id=document_id,  # Must exist from upstream
                    content=c["content"],
                    embedding=c["embedding"],
                    chunk_index=i,
                    file_page_number=1,  # Default for raw institutional ingest if not mapped
                    metadata={
                        **c["metadata"],
                        "institution_id": tenant_id,
                        "is_global": False,  # Strict isolation
                    },
                )
            )

        # Batch insert using uniform repository (wrapped for async safety)
        import asyncio

        await asyncio.to_thread(repo.save_chunks_sync, domain_chunks) if hasattr(
            repo, "save_chunks_sync"
        ) else await repo.save_chunks(domain_chunks)

        # 2. Finalize Document Status
        source_repo = SupabaseSourceRepository()

        # Robust Metadata Update
        current_doc = await source_repo.get_by_id(document_id)
        current_meta = current_doc.get("metadata", {}) if current_doc else {}

        # Update metadata state
        current_meta.update(
            {"status": IngestionStatus.SUCCESS.value, "chunks_count": len(domain_chunks)}
        )

        await source_repo.update_status_and_metadata(
            document_id, IngestionStatus.SUCCESS.value, current_meta
        )

        return {"status": IngestionStatus.SUCCESS.value, "indexed_count": len(domain_chunks)}

    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Indexing failed: {str(e)}"}
