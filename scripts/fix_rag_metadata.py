
import asyncio
import os
import json
from pprint import pprint

# Manual env backfill for Supabase
# Import settings AFTER setting env vars if needed
if "NEXT_PUBLIC_SUPABASE_URL" in os.environ and "SUPABASE_URL" not in os.environ:
    os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]

if "SUPABASE_SERVICE_ROLE_KEY" in os.environ and "SUPABASE_SERVICE_KEY" not in os.environ:
    os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

from app.infrastructure.supabase.client import get_async_supabase_client

TENANT_ID = os.getenv("TENANT_ID")

STANDARD_MAPPING = {
    "ISO 9001": "ISO 9001",
    "ISO 45001": "ISO 45001",
    "ISO 14001": "ISO 14001",
    "ISO-9001": "ISO 9001",
    "ISO-45001": "ISO 45001",
    "ISO-14001": "ISO 14001",
}

async def backfill_metadata():
    print("🔌 Connecting to Supabase...")
    supabase = await get_async_supabase_client()
    
    # 1. Fetch chunks without source_standard or with null source_standard
    # Note: It's hard to filter by JSON key existence in simple PostgREST
    # We will fetch chunks for the tenant and process in batches
    
    print(f"🔍 Fetching chunks for tenant: {TENANT_ID}")
    
    page = 0
    page_size = 100
    updated_count = 0
    total_processed = 0
    
    while True:
        start = page * page_size
        end = start + page_size - 1
        
        print(f"   Processing batch {page + 1} (rows {start}-{end})...")
        
        response = await (
            supabase.table("content_chunks")
            .select("id, metadata, content")
            .eq("institution_id", TENANT_ID)
            .range(start, end)
            .execute()
        )
        
        rows = response.data
        if not rows:
            break
            
        for row in rows:
            total_processed += 1
            chunk_id = row['id']
            meta = row['metadata']
            
            if isinstance(meta, str):
                meta = json.loads(meta)
                
            original_meta = meta.copy()
            needs_update = False
            
            # Heuristic 1: Use 'scope' if present
            if 'source_standard' not in meta:
                scope = meta.get('scope', '')
                for key, std in STANDARD_MAPPING.items():
                    if key in scope:
                        meta['source_standard'] = std
                        needs_update = True
                        break
            
            # Heuristic 2: Use 'clause_title'
            if not needs_update and 'source_standard' not in meta:
                 clause_title = meta.get('clause_title', '')
                 for key, std in STANDARD_MAPPING.items():
                    if key in clause_title:
                        meta['source_standard'] = std
                        needs_update = True
                        break

            # Heuristic 3: Use content (more aggressive)
            if not needs_update and 'source_standard' not in meta:
                content = row.get('content', '')
                # Search first 500 chars
                content_head = content[:500]
                for key, std in STANDARD_MAPPING.items():
                    if key in content_head: 
                        meta['source_standard'] = std
                        needs_update = True
                        break
            
            # Heuristic 4: Collection ID mapping (Hardcoded for this tenant)
            if not needs_update:
                col_id = row.get('collection_id') 
                if not col_id:
                    col_id = meta.get('collection_id')
                
                if col_id == '48a5c920-59f1-4498-9683-727339463ad2':
                     # This is the ISO-Trinorma collection.
                     # We can try to infer from content more aggressively.
                     content = row.get('content', '')
                     content_head = content[:1000] # Increased window
                     
                     # Check for ISO 9001
                     if "ISO 9001" in content_head or "ISO 9001" in meta.get('scope', ''):
                         if meta.get('source_standard') != 'ISO 9001':
                            meta['source_standard'] = 'ISO 9001'
                            needs_update = True
                     
                     # Check for ISO 45001
                     elif "ISO 45001" in content_head or "ISO 45001" in meta.get('scope', ''):
                         if meta.get('source_standard') != 'ISO 45001':
                            meta['source_standard'] = 'ISO 45001'
                            needs_update = True

                     # Check for ISO 14001
                     elif "ISO 14001" in content_head or "ISO 14001" in meta.get('scope', ''):
                         if meta.get('source_standard') != 'ISO 14001':
                            meta['source_standard'] = 'ISO 14001'
                            needs_update = True
            
            # Remove "source_standard" check from top-level heuristic 4 block to allow overwriting/enriching even if present but maybe wrong?
            # No, keep it safe for now: only if missing OR if we are sure it's wrong.
            # The above logic handles "if != ..." so it's safe.

            if needs_update:
                # Update the chunk
                print(f"   ✏️ Updating chunk {chunk_id} -> {meta.get('source_standard')}")
                try:
                    await (
                        supabase.table("content_chunks")
                        .update({"metadata": meta})
                        .eq("id", chunk_id)
                        .execute()
                    )
                    updated_count += 1
                except Exception as e:
                    print(f"❌ Failed to update {chunk_id}: {e}")
        
        page += 1
        if len(rows) < page_size:
            break
            
    print(f"✅ Backfill complete. Processed {total_processed} chunks. Updated {updated_count} chunks.")

if __name__ == "__main__":
    asyncio.run(backfill_metadata())
