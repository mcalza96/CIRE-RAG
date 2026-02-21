import asyncio
from datetime import datetime
from app.infrastructure.supabase.client import get_async_supabase_client

async def run():
    doc_id = "a22efae0-4c8a-4561-b93b-1eb30dafc8f2"
    client = await get_async_supabase_client()
    
    print(f"👀 Monitoring doc {doc_id}...")
    
    last_event_id = None
    
    for _ in range(30): # Poll for 5 minutes (every 10s)
        # Fetch status
        doc_res = await client.table("source_documents").select("status, metadata").eq("id", doc_id).maybe_single().execute()
        doc = doc_res.data
        if doc:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Status: {doc['status']}")
            if doc['status'] in ["processed", "success", "failed", "error"]:
                print(f"🏁 Final state reached: {doc['status']}")
        
        # Fetch new events
        query = client.table("ingestion_events").select("*").eq("source_document_id", doc_id).order("created_at", desc=False)
        if last_event_id:
            # Simple polling: just print all for now since it's a small number
            pass
        
        events_res = await query.execute()
        events = events_res.data or []
        for e in events:
            # Only print if newer than what we've seen (simplified)
            print(f"  - {e['created_at']}: {e['message']} ({e['status']})")
        
        if doc and doc['status'] in ["processed", "success", "failed", "error"]:
            break
            
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run())
