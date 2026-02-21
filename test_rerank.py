import asyncio
from app.ai.rerankers.gravity_reranker import GravityReranker
from app.infrastructure.supabase.client import get_supabase_client

async def main():
    client = get_supabase_client()
    res = client.table("content_chunks").select("content").eq("id", "a067b196-666f-4845-8b61-b737b439ed5a").execute()
    content = res.data[0]["content"] if res.data else None
    print(f"Content: {content[:200] if content else 'None'}")
    
    reranker = GravityReranker()
    score = reranker._heading_boost("que dice la introduccion del documento?", content or "")
    print(f"Heading boost: {score}")

if __name__ == "__main__":
    asyncio.run(main())
