
import asyncio
import os
import json

# Manual env backfill for Supabase
if "NEXT_PUBLIC_SUPABASE_URL" in os.environ and "SUPABASE_URL" not in os.environ:
    os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]

if "SUPABASE_SERVICE_ROLE_KEY" in os.environ and "SUPABASE_SERVICE_KEY" not in os.environ:
    os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# Import client after setting env vars
from app.infrastructure.supabase.client import get_async_supabase_client

COLLECTION_IDS = [
    "f1ed8bf5-2a27-4607-922e-eaeac9b73aab", # Trinorma
    "48a5c920-59f1-4498-9683-727339463ad2", # ISO-Trinorma
    "3b207791-e2e5-4b21-9c14-b01d93343b00"  # Pre-calculo
]

async def cleanup_collections():
    print("🔌 Connecting to Supabase...")
    supabase = await get_async_supabase_client()
    
    for col_id in COLLECTION_IDS:
        print(f"\n🧹 Cleaning up Collection ID: {col_id}")
        
        try:
            # 1. Delete Chunks
            print(f"   🗑️ Deleting chunks for collection {col_id}...")
            await (
                supabase.table("content_chunks")
                .delete()
                .eq("collection_id", col_id)
                .execute()
            )
            print(f"   ✅ Chunks deleted.")

            # 2. Delete Source Documents
            print(f"   🗑️ Deleting source documents for collection {col_id}...")
            await (
                supabase.table("source_documents")
                .delete()
                .eq("collection_id", col_id)
                .execute()
            )
            print(f"   ✅ Source documents deleted.")

            # 3. Delete Ingestion Batches
            print(f"   🗑️ Deleting ingestion batches for collection {col_id}...")
            await (
                supabase.table("ingestion_batches")
                .delete()
                .eq("collection_id", col_id)
                .execute()
            )
            print(f"   ✅ Ingestion batches deleted.")

            # 4. Delete Collection
            print(f"   🗑️ Deleting collection entry {col_id}...")
            await (
                supabase.table("collections")
                .delete()
                .eq("id", col_id)
                .execute()
            )
            print(f"   ✅ Collection metadata deleted.")

        except Exception as e:
            print(f"   ❌ Error during cleanup: {e}")

if __name__ == "__main__":
    asyncio.run(cleanup_collections())
