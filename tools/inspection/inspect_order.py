import asyncio
import json
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    client = await get_async_supabase_client()
    res = await client.table('content_chunks').select('id, content, chunk_index, file_page_number, metadata').eq('source_id', 'a22efae0-4c8a-4561-b93b-1eb30dafc8f2').order('chunk_index').limit(10).execute()
    if not res.data:
        print("Empty results")
        return
    for c in res.data:
        snippet = c['content'].replace('\n', ' ')[:50]
        meta_type = c.get('metadata', {}).get('type', 'N/A')
        print(f"Index {c['chunk_index']} | Page {c['file_page_number']} | Type: {meta_type} | Content: {snippet}...")

if __name__ == "__main__":
    asyncio.run(run())
