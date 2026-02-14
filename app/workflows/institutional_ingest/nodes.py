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
PARSE_WINDOW_CONCURRENCY = max(1, min(10, int(getattr(settings, "WORKER_PER_TENANT_CONCURRENCY", 5))))

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
            user_content = f"CONTENIDO A ESTRUCTURAR (parte {i+1}/{len(windows)}):\n\n{window}"

            # Parallel parsing with bounded concurrency to avoid rate/memory spikes.
            async with semaphore:
                try:
                    response = await llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_content)
                    ])
                except Exception as exc:
                    raise RuntimeError(
                        f"Window parsing failed at part {i+1}/{len(windows)}: {str(exc)}"
                    ) from exc

            return str(response.content)

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
            semantic_chunks.append({
                "content": c['content'],
                "embedding": c['embedding'],
                "metadata": {
                   "char_start": c['char_start'],
                   "char_end": c['char_end']
                }
            })
            
        return {"semantic_chunks": semantic_chunks}
    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Embedding failed: {str(e)}"}

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
            domain_chunks.append(ContentChunk(
                source_id=document_id, # Must exist from upstream
                content=c["content"],
                embedding=c["embedding"],
                chunk_index=i,
                file_page_number=1, # Default for raw institutional ingest if not mapped
                metadata={
                    **c["metadata"],
                    "institution_id": tenant_id,
                    "is_global": False # Strict isolation
                }
            ))
            
        # Batch insert using uniform repository (wrapped for async safety)
        import asyncio
        await asyncio.to_thread(repo.save_chunks_sync, domain_chunks) if hasattr(repo, 'save_chunks_sync') else await repo.save_chunks(domain_chunks)
        
        # 2. Finalize Document Status
        source_repo = SupabaseSourceRepository()
        
        # Robust Metadata Update
        current_doc = await source_repo.get_by_id(document_id)
        current_meta = current_doc.get("metadata", {}) if current_doc else {}
        
        # Update metadata state
        current_meta.update({
             "status": IngestionStatus.SUCCESS.value,
             "chunks_count": len(domain_chunks)
        })
        
        await source_repo.update_status_and_metadata(
            document_id, 
            IngestionStatus.SUCCESS.value, 
            current_meta
        )
            
        return {"status": IngestionStatus.SUCCESS.value, "indexed_count": len(domain_chunks)}
        
    except Exception as e:
        return {"status": IngestionStatus.FAILED.value, "error": f"Indexing failed: {str(e)}"}
