import asyncio
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    client = await get_async_supabase_client()
    res = await client.table('content_chunks').select('file_page_number').eq('source_id', 'a22efae0-4c8a-4561-b93b-1eb30dafc8f2').limit(1).execute()
    if res.data:
        v = res.data[0]['file_page_number']
        print(f'Type: {type(v)}, Value: {v}')

if __name__ == "__main__":
    asyncio.run(run())
