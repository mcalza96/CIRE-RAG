import asyncio
import httpx
from app.core.settings import settings

async def run():
    doc_id = "a22efae0-4c8a-4561-b93b-1eb30dafc8f2"
    tenant_id = "b18a053c-1787-4a43-ac97-60c459f455b8"
    
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
