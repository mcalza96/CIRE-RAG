
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

async def list_collections():
    print("🔌 Connecting to Supabase...")
    supabase = await get_async_supabase_client()
    
    print(f"🔍 Fetching collections for institution: {TENANT_ID}")
    
    try:
        print(f"🔍 Fetching collections for tenant_id: {TENANT_ID}")
        response = await (
            supabase.table("collections")
            .select("*")
            .eq("tenant_id", TENANT_ID)
            .execute()
        )
        
        rows = response.data
        if not rows:
            print("⚠️ No collections found!")
        else:
            print(f"✅ Found {len(rows)} collections:")
            for row in rows:
                print("-" * 40)
                pprint(row)
                
    except Exception as e:
        print(f"❌ Failed to fetch collections: {e}")

if __name__ == "__main__":
    asyncio.run(list_collections())
