
import asyncio
import os
import json
from pprint import pprint

# Manual env backfill for Supabase
if "NEXT_PUBLIC_SUPABASE_URL" in os.environ and "SUPABASE_URL" not in os.environ:
    os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]

if "SUPABASE_SERVICE_ROLE_KEY" in os.environ and "SUPABASE_SERVICE_KEY" not in os.environ:
    os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

from app.infrastructure.supabase.client import get_async_supabase_client

TARGET_COLLECTION_ID = "5cdcd14c-c256-41b3-ade0-f93b73d71429"

STANDARD_MAPPING = {
    "iso 9001": "ISO 9001",
    "iso 14001": "ISO 14001",
    "iso 45001": "ISO 45001",
    "iso 31000": "ISO 31000",
    "iso 27001": "ISO 27001"
}

async def fix_source_metadata():
    print("🔌 Connecting to Supabase...")
    supabase = await get_async_supabase_client()
    
    print(f"🔍 Fetching source_documents for collection: {TARGET_COLLECTION_ID}")
    
    response = await (
        supabase.table("source_documents")
        .select("id, metadata")
        .eq("collection_id", TARGET_COLLECTION_ID)
        .execute()
    )
    
    rows = response.data
    if not rows:
        print("⚠️ No source documents found!")
        return

    print(f"✅ Found {len(rows)} documents. Analyzing...")
    
    updated_count = 0
    
    for row in rows:
        doc_id = row['id']
        meta = row.get('metadata') or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                print(f"⚠️ Failed to parse metadata string for {doc_id}")
                continue
                
        print(f"DEBUG: Keys in meta for {doc_id}: {list(meta.keys())}")
        title = (meta.get('title') or "").lower()
        
        # Normalize title for matching (replace _ and - with space)
        normalized_title = title.replace("_", " ").replace("-", " ")
        
        detected_standard = None
        for key, std in STANDARD_MAPPING.items():
            if key in normalized_title:
                detected_standard = std
                break
        
        if not detected_standard:
            print(f"⚠️ Could not detect standard for title='{title}'")
            continue
            
        # Check if update is needed
        current_std = meta.get("source_standard")
        current_scope = meta.get("scope")
        
        needs_update = False
        if current_std != detected_standard:
            meta["source_standard"] = detected_standard
            needs_update = True
            
        # Also ensure 'scope' has it, as some filters use that
        if not current_scope or detected_standard not in current_scope:
            # simple append or set
            if current_scope:
                meta["scope"] = f"{current_scope} {detected_standard}"
            else:
                meta["scope"] = detected_standard
            needs_update = True

        if needs_update:
            print(f"✏️ Updating {doc_id} ('{title}'):")
            print(f"   -> source_standard: {detected_standard}")
            
            try:
                await (
                    supabase.table("source_documents")
                    .update({"metadata": meta})
                    .eq("id", doc_id)
                    .execute()
                )
                updated_count += 1
                print("   ✅ Updated.")
            except Exception as e:
                print(f"   ❌ Failed to update: {e}")
        else:
            print(f"👌 {doc_id} already has correct metadata.")

    print(f"✨ Backfill complete. Updated {updated_count} documents.")

if __name__ == "__main__":
    asyncio.run(fix_source_metadata())
