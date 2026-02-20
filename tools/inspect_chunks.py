import asyncio
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    client = await get_async_supabase_client()
    res = await client.table('content_chunks').select('content, chunk_index, file_page_number').eq('source_id', 'a22efae0-4c8a-4561-b93b-1eb30dafc8f2').gte('file_page_number', 4).lte('file_page_number', 6).order('chunk_index').execute()
    for c in res.data:
        print(f"--- {c['chunk_index']} (p.{c['file_page_number']}) ---")
        print(c['content'])
        print("\n")

if __name__ == "__main__":
    asyncio.run(run())
