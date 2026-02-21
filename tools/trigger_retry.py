import asyncio
import httpx
import sys
import os
from app.infrastructure.settings import settings

async def run():
    if len(sys.argv) < 2:
        print("Usage: python trigger_retry.py <doc_id> [tenant_id]")
        return

    doc_id = sys.argv[1]
    tenant_id = sys.argv[2] if len(sys.argv) > 2 else os.getenv("TENANT_ID")
    
    if not tenant_id:
        print("Error: tenant_id must be provided as 2nd arg or via TENANT_ID env var")
        return
    
    # We can use the internal host since we are local
    url = f"http://localhost:8000/api/v1/ingestion/retry/{doc_id}"
    headers = {
        "X-Tenant-ID": tenant_id,
        "X-Service-Secret": settings.RAG_API_KEY
    }
    
    async with httpx.AsyncClient() as client:
        print(f"🚀 Triggering retry for {doc_id}...")
        resp = await client.post(url, headers=headers)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")

if __name__ == "__main__":
    asyncio.run(run())
