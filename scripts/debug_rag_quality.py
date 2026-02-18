
import asyncio
import os
import json
from pprint import pprint
# Import settings AFTER setting env vars if needed
# But we need to debug first
print("DEBUG: Current Environment Variables (Supabase related):")
for k, v in os.environ.items():
    if "SUPABASE" in k:
        print(f"{k} = {v[:10]}...")

# Manually backfill if needed because pydantic might be tricky
if "NEXT_PUBLIC_SUPABASE_URL" in os.environ and "SUPABASE_URL" not in os.environ:
    print("DEBUG: Backfilling SUPABASE_URL from NEXT_PUBLIC_SUPABASE_URL")
    os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]

if "SUPABASE_SERVICE_ROLE_KEY" in os.environ and "SUPABASE_SERVICE_KEY" not in os.environ:
    print("DEBUG: Backfilling SUPABASE_SERVICE_KEY from SUPABASE_SERVICE_ROLE_KEY")
    os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

from app.core.settings import settings
from app.infrastructure.supabase.client import get_async_supabase_client

async def main():
    print(f"DEBUG: settings.SUPABASE_URL = {settings.SUPABASE_URL}")
    print("🔌 Connecting to Supabase via Client...")
    
    try:
        supabase = await get_async_supabase_client()
        print("✅ Client initialized!")

        tenant_id = "b18a053c-1787-4a43-ac97-60c459f455b8"
        
        # Check if ISO 9001 exists
        print(f"🔍 Checking for ANY 'ISO 9001' chunks in tenant...")
        
        # We'll use a semantic search via raw SQL if possible, but with client we have to rely on eq
        # But `source_standard` is in a JSONB column. Supabase-py support for JSON filtering is tricky.
        # We can try .eq("metadata->>source_standard", "ISO 9001") if the client supports it,
        # or just fetch a sample of different collection.
        
        # Verify specific updated chunks
        updated_ids = [
            "fd6be5dd-4f25-4bae-87db-3b65011732aa",
            "11d70f4d-e519-4967-8ae9-6c2cef0a60bd",
            "1934f1ea-5796-4ac1-bb96-561a8d3a1956"
        ]
        
        print(f"🔍 Verifying updated chunks: {updated_ids}")
        
        response = await (
            supabase.table("content_chunks")
            .select("id, metadata, content")
            .in_("id", updated_ids)
            .execute()
        )
        
        rows = response.data
        if not rows:
            print("⚠️ No chunks found!")
        else:
            print(f"✅ Found {len(rows)} chunks:")
            for row in rows:
                print("-" * 40)
                print(f"ID: {row.get('id')}")
                print("Metadata:")
                pprint(row.get('metadata'))
        
        rows = response.data
        
        if not rows:
            print("⚠️ No chunks found for this tenant!")
        else:
            print(f"✅ Found {len(rows)} sample chunks:")
            for row in rows:
                print("-" * 40)
                print(f"ID: {row.get('id')}")
                print(f"Institution ID: {row.get('institution_id')}")
                print(f"Collection ID: {row.get('collection_id')}")
                content = row.get('content') or ""
                print(f"Content Preview: {content[:50]}...")
                print("Metadata:")
                meta = row.get('metadata')
                if meta:
                     pprint(meta if isinstance(meta, dict) else json.loads(meta))
                else:
                    print("None")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
