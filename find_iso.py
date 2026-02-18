import asyncio
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    client = await get_async_supabase_client()
    res = await client.table('source_documents').select('id, filename, status').ilike('filename', '%ISO%9001%').execute()
    print(res.data)

if __name__ == "__main__":
    asyncio.run(run())
