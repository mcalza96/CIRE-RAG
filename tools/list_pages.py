import asyncio
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    client = await get_async_supabase_client()
    res = await client.table('content_chunks').select('file_page_number').eq('source_id', 'a22efae0-4c8a-4561-b93b-1eb30dafc8f2').execute()
    pages = sorted(set(int(c['file_page_number']) for c in res.data if c['file_page_number'] is not None))
    print(f'Pages: {pages}')

if __name__ == "__main__":
    asyncio.run(run())
