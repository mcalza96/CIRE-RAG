import asyncio
from httpx import AsyncClient

async def run():
    async with AsyncClient(base_url="http://localhost:8000") as client:
        payload = {
            "query": "que dice la introduccion del documento?",
            "tenant_id": "289007d1-07b1-40ca-bd8f-700d5c8659e7",
            "collection_id": "8c29a898-a27d-49c7-8123-7ce3b8d14be5",
            "k": 12,
            "fetch_k": 60,
            "filters": {},
            "rerank": {"enabled": True},
            "graph": {"max_hops": 2}
        }
        res = await client.post("/api/v1/retrieval/comprehensive", json=payload, headers={"Authorization": "Bearer cire-service-secret"})
        data = res.json()
        
        items = data.get("items", [])
        print(f"Total items returned: {len(items)}")
        for i, item in enumerate(items[:5]):
            print(f"[{i}] {item.get('source')} - score: {item.get('score')} - layer: {item.get('metadata', {}).get('source_layer')}")
            
if __name__ == "__main__":
    asyncio.run(run())
