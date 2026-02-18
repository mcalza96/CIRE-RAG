import asyncio
import json
import os
from typing import List, Dict, Any
from app.infrastructure.supabase.client import get_async_supabase_client

DOC_ID = "a22efae0-4c8a-4561-b93b-1eb30dafc8f2"

async def audit_chunks():
    client = await get_async_supabase_client()
    
    # 1. Fetch source document metadata
    doc_res = await client.table("source_documents").select("*").eq("id", DOC_ID).maybe_single().execute()
    doc = doc_res.data
    if not doc:
        print(f"❌ Document {DOC_ID} not found.")
        return

    print(f"📄 Document: {doc['filename']}")
    print(f"📊 Status: {doc['status']}")
    print(f"📅 Created At: {doc['created_at']}")
    
    # 2. Fetch chunks
    chunks_res = await client.table("content_chunks").select("id, content, chunk_index, file_page_number").eq("source_id", DOC_ID).order("chunk_index").execute()
    chunks = chunks_res.data or []
    
    unique_pages = sorted(list(set(c['file_page_number'] for c in chunks if c['file_page_number'] is not None)))
    print(f"📄 Unique Pages Found: {unique_pages}")
    
    print(f"🧩 Total Chunks: {len(chunks)}")
    
    if not chunks:
        print("⚠️ No chunks found.")
        return

    # 3. Analyze sizes and artifacts
    lengths = [len(c['content']) for c in chunks]
    avg_len = sum(lengths) / len(lengths)
    min_len = min(lengths)
    max_len = max(lengths)
    
    print(f"📏 Chunk lengths - Avg: {avg_len:.1f}, Min: {min_len}, Max: {max_len}")
    
    # 4. Check for TOC artifacts (common issue)
    toc_indicators = ["índice", "tabla de contenido", "página", "........"]
    toc_chunks = []
    
    for c in chunks:
        content_lower = c['content'].lower()
        if any(ind in content_lower for ind in toc_indicators) and len(c['content']) < 500:
          if "........" in content_lower:
            toc_chunks.append(c)

    print(f"📑 Potential TOC artifact chunks found: {len(toc_chunks)}")
    for i, tc in enumerate(toc_chunks[:3]):
        snippet = tc['content'].replace('\n', ' ')[:100]
        print(f"   [{i}] Chunk {tc['chunk_index']} (p.{tc['file_page_number']}): {snippet}...")

    print(f"\n🔍 Inspection of first 5 chunks:")
    for i, c in enumerate(chunks[:5]):
        print(f"--- Chunk {c['chunk_index']} (p.{c['file_page_number']}) ---")
        print(c['content'])
        print("-" * 40)

    print(f"\n📑 Inspection of pages 4-6 (TOC):")
    target_pages = [4, 5, 6]
    page_chunks = [c for c in chunks if c.get('file_page_number') is not None and int(c['file_page_number']) in target_pages]
    print(f"   Found {len(page_chunks)} chunks for pages 4-6")
    for c in page_chunks:
        print(f"--- Chunk {c['chunk_index']} (p.{c['file_page_number']}) ---")
        print(c['content'][:200] + "...")
        print("-" * 40)

    # Check mapping metadata
    if chunks:
        print(f"\n🏷️ Sample Metadata (Chunk {chunks[0]['chunk_index']}):")
        # We need to fetch metadata separately if it wasn't in the initial select
        # Wait, I added metadata to select in inspect_order.py but not in audit_iso_ingestion.py
        doc_meta_res = await client.table("content_chunks").select("metadata").eq("id", chunks[0]['id']).maybe_single().execute()
        print(json.dumps(doc_meta_res.data.get('metadata', {}), indent=2))

    # 5. Save report
    report = {
        "doc_id": DOC_ID,
        "filename": doc['filename'],
        "chunk_count": len(chunks),
        "avg_length": avg_len,
        "toc_artifacts": len(toc_chunks),
        "timestamp": doc['created_at']
    }
    
    with open("iso_audit_baseline.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n✅ Baseline saved to iso_audit_baseline.json")

if __name__ == "__main__":
    asyncio.run(audit_chunks())
