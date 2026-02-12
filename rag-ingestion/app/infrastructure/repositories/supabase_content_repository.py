from typing import List, Dict, Any
import structlog
from app.domain.repositories.content_repository import IContentRepository
from app.infrastructure.supabase.client import get_async_supabase_client

logger = structlog.get_logger(__name__)

class SupabaseContentRepository(IContentRepository):
    """
    Concrete implementation of IContentRepository for Supabase.
    """
    
    def __init__(self):
        self._supabase = None
        
    async def get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def delete_chunks_by_source_id(self, source_id: str) -> None:
        if not source_id:
            return
        
        client = await self.get_client()
        logger.info(f"Cleaning up legacy chunks for source {source_id} (Batched)...")
        
        try:
            # 1. Fetch all IDs for this source
            res = await client.table("content_chunks").select("id").eq("source_id", source_id).execute()
            ids = [r['id'] for r in res.data]
            
            if not ids:
                logger.info(f"No legacy chunks found for source {source_id}.")
                return
            
            total = len(ids)
            batch_size = 500
            logger.info(f"Purging {total} chunks in batches of {batch_size}...")
            
            for i in range(0, total, batch_size):
                batch_ids = ids[i:i+batch_size]
                await client.table("content_chunks").delete().in_("id", batch_ids).execute()
                logger.info(f"Purged batch {int(i/batch_size)+1}/{(total+batch_size-1)//batch_size}")
                import asyncio
                await asyncio.sleep(0.1) # Breather
                
            logger.info(f"✅ Legacy chunks for source {source_id} purged successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to purge chunks for source {source_id}: {e}")
            raise e

    async def save_chunks(self, chunks: List[Any]) -> int:
        if not chunks:
            return 0

        from app.infrastructure.mappers.persistence_mapper import PersistenceMapper
        
        client = await self.get_client()
        batch_size = 50  # Increased to 50 for speed, assuming 10 was too conservative
        
        logger.info(f"Persisting {len(chunks)} chunks to Supabase...")
        print(f"[SupabaseRepo] DEBUG: Persisting {len(chunks)} chunks...")
        
        # Optimization (Pattern 5): Use list comprehension for faster processing
        # Resilience Guard: Filter out chunks with empty or None embeddings (Fix: 22000 dimension error)
        
        # FIX: Handle Pydantic models by converting to dict/accessing safely
        def get_embedding(c):
            if hasattr(c, "embedding"): return c.embedding
            if isinstance(c, dict): return c.get("embedding")
            return None

        # Convert everything to dicts first for uniform handling by mapper
        # If it's a Pydantic model, use model_dump() (v2) or dict() (v1)
        # PersistenceMapper.map_to_sql likely handles specific types, but let's normalize here if needed.
        # Actually PersistenceMapper.map_to_sql expects the object + table name. 
        # But the filter below accesses .get() which breaks on objects.
        
        valid_chunks = []
        for c in chunks:
            emb = get_embedding(c)
            if emb and len(emb) > 0:
                valid_chunks.append(c)
        
        if chunks and not valid_chunks:
            logger.error(f"❌ Critical Failure: All {len(chunks)} chunks were filtered out due to missing embeddings.")
            raise RuntimeError(f"Audit Persistence Failure: document chunks lost due to empty embeddings.")

        if len(valid_chunks) < len(chunks):
            logger.warning(f"⚠️ Filtered {len(chunks) - len(valid_chunks)} chunks with empty embeddings.")

        normalized_chunks = [PersistenceMapper.map_to_sql(c, "content_chunks") for c in valid_chunks]

        total_chunks = len(normalized_chunks)
        total_batches = (total_chunks + batch_size - 1) // batch_size
        
        logger.info(f"🚀 Starting persistence of {total_chunks} chunks in {total_batches} batches...")

        for i in range(0, total_chunks, batch_size):
            batch = normalized_chunks[i:i + batch_size]
            current_batch_index = (i // batch_size) + 1
            
            try:
                await client.table("content_chunks").insert(batch).execute()
                
                # Calculate progress
                progress_pct = int((min(i + batch_size, total_chunks) / total_chunks) * 100)
                logger.info(f"💾 [Persistence] Batch {current_batch_index}/{total_batches} uploaded ({progress_pct}%).")
                print(f"[SupabaseRepo] DEBUG: Batch {current_batch_index}/{total_batches} uploaded.")
                
                # Yield to event loop to prevent blocking and allow keep-alives
                import asyncio
                await asyncio.sleep(0.01) # Faster yield
                
            except Exception as e:
                logger.error(f"❌ Failed to insert batch {current_batch_index}: {e}")
                raise e
        
        logger.info(f"✅ {total_chunks} chunks persisted successfully.")
        return total_chunks
