
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

# Target filter from the logs
TARGET_FILTERS = {
    "collection_id": "5cdcd14c-c256-41b3-ade0-f93b73d71429", # The one in logs
    "source_standards": ["iso 9001", "iso 14001", "iso 45001"]
}

async def check_source_metadata():
    print("🔌 Connecting to Supabase...")
    supabase = await get_async_supabase_client()
    
    col_id = TARGET_FILTERS["collection_id"]
    print(f"🔍 Checking source_documents for collection: {col_id}")
    
    response = await (
        supabase.table("source_documents")
        .select("id, metadata, created_at")
        .eq("collection_id", col_id)
        .execute()
    )
    
    rows = response.data
    if not rows:
        print("⚠️ No source documents found for this collection!")
        return

    print(f"✅ Found {len(rows)} source documents. Checking metadata against filters...")
    
    for row in rows:
        meta = row.get("metadata", {})
        print("-" * 40)
        print(f"ID: {row['id']}")
        print("Metadata:")
        pprint(meta)
        
        # Simulate the filtering logic from atomic_engine.py
        candidate_values = [
            meta.get("source_standard"),
            meta.get("standard"),
            meta.get("scope"),
            meta.get("norma"),
        ]
        
        row_scope = ""
        for value in candidate_values:
            if isinstance(value, str) and value.strip():
                row_scope = value.strip().lower()
                break
        
        print(f"Detected Scope: '{row_scope}'")
        
        matches = False
        if row_scope:
            matches = any(target in row_scope for target in TARGET_FILTERS["source_standards"])
        
        print(f"Matches Filter {TARGET_FILTERS['source_standards']}? {'✅ YES' if matches else '❌ NO'}")

if __name__ == "__main__":
    asyncio.run(check_source_metadata())
