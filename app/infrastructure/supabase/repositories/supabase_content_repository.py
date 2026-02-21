from typing import List, Dict, Any
import structlog
from app.domain.repositories.content_repository import IContentRepository
from app.infrastructure.observability.context_vars import get_tenant_id
from app.infrastructure.settings import settings
from app.infrastructure.supabase.client import get_async_supabase_client
from app.infrastructure.observability.ingestion_logging import compact_error, emit_event

logger = structlog.get_logger(__name__)


class SupabaseContentRepository(IContentRepository):
    """
    Concrete implementation of IContentRepository for Supabase.
    """

    @staticmethod
    def _tenant_from_context() -> str:
        return str(get_tenant_id() or "").strip()

    def __init__(self):
        self._supabase = None

    async def get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def delete_chunks_by_source_id(self, source_id: str) -> None:
        if not source_id:
            return
        tenant_id = self._tenant_from_context()

        client = await self.get_client()
        emit_event(logger, "legacy_chunk_cleanup_started", source_id=source_id, tenant_id=tenant_id)

        try:
            # 1. Fetch all IDs for this source
            query = client.table("content_chunks").select("id").eq("source_id", source_id)
            if tenant_id:
                query = query.eq("institution_id", tenant_id)
            res = await query.execute()
            ids = [r["id"] for r in res.data]

            if not ids:
                emit_event(
                    logger, "legacy_chunk_cleanup_skipped", source_id=source_id, reason="no_chunks"
                )
                return

            total = len(ids)
            batch_size = 500
            emit_event(
                logger,
                "legacy_chunk_cleanup_batch_plan",
                source_id=source_id,
                total_chunks=total,
                batch_size=batch_size,
            )

            for i in range(0, total, batch_size):
                batch_ids = ids[i : i + batch_size]
                await client.table("content_chunks").delete().in_("id", batch_ids).execute()
                emit_event(
                    logger,
                    "legacy_chunk_cleanup_batch_done",
                    source_id=source_id,
                    batch_index=int(i / batch_size) + 1,
                    total_batches=(total + batch_size - 1) // batch_size,
                )
                import asyncio

                await asyncio.sleep(0.1)  # Breather

            emit_event(
                logger,
                "legacy_chunk_cleanup_completed",
                source_id=source_id,
                total_chunks=total,
            )
        except Exception as e:
            emit_event(
                logger,
                "legacy_chunk_cleanup_failed",
                level="error",
                source_id=source_id,
                error=compact_error(e),
            )
            raise e

    async def save_chunks(self, chunks: List[Any]) -> int:
        if not chunks:
            return 0
        tenant_id = self._tenant_from_context()

        from app.infrastructure.supabase.mappers.persistence_mapper import PersistenceMapper

        client = await self.get_client()
        batch_size = max(10, int(getattr(settings, "CONTENT_CHUNKS_INSERT_BATCH_SIZE", 100) or 100))
        inter_batch_sleep = max(
            0.0,
            float(getattr(settings, "CONTENT_CHUNKS_INSERT_BATCH_SLEEP_SECONDS", 0.0) or 0.0),
        )

        emit_event(
            logger, "chunk_persistence_started", requested_chunks=len(chunks), tenant_id=tenant_id
        )

        # Optimization (Pattern 5): Use list comprehension for faster processing
        # Resilience Guard: Filter out chunks with empty or None embeddings (Fix: 22000 dimension error)

        # FIX: Handle Pydantic models by converting to dict/accessing safely
        def get_embedding(c):
            if hasattr(c, "embedding"):
                return c.embedding
            if isinstance(c, dict):
                return c.get("embedding")
            return None

        # Convert everything to dicts first for uniform handling by mapper
        # If it's a Pydantic model, use model_dump() (v2) or dict() (v1)
        # PersistenceMapper.map_to_sql likely handles specific types, but let's normalize here if needed.
        # Actually PersistenceMapper.map_to_sql expects the object + table name.
        # But the filter below accesses .get() which breaks on objects.

        valid_chunks = []
        for c in chunks:
            emb = get_embedding(c)
            metadata = c.get("metadata") if isinstance(c, dict) else getattr(c, "metadata", {})
            retrieval_eligible = True
            if isinstance(metadata, dict):
                retrieval_eligible = bool(metadata.get("retrieval_eligible", True))

            if emb and len(emb) > 0:
                valid_chunks.append(c)
                continue
            if not retrieval_eligible:
                valid_chunks.append(c)

        if chunks and not valid_chunks:
            emit_event(
                logger,
                "chunk_persistence_filtered_all",
                level="error",
                requested_chunks=len(chunks),
                reason="missing_embeddings",
            )
            raise RuntimeError(
                f"Audit Persistence Failure: document chunks lost due to empty embeddings."
            )

        if len(valid_chunks) < len(chunks):
            emit_event(
                logger,
                "chunk_persistence_filtered_partial",
                level="warning",
                requested_chunks=len(chunks),
                valid_chunks=len(valid_chunks),
                filtered_chunks=len(chunks) - len(valid_chunks),
            )

        normalized_chunks = []
        for chunk in valid_chunks:
            row = PersistenceMapper.map_to_sql(chunk, "content_chunks")
            row_tenant = str(row.get("institution_id") or "").strip()
            if tenant_id and row_tenant and row_tenant != tenant_id:
                raise ValueError("TENANT_MISMATCH")
            if not row_tenant and tenant_id:
                row["institution_id"] = tenant_id
            normalized_chunks.append(row)

        total_chunks = len(normalized_chunks)
        total_batches = (total_chunks + batch_size - 1) // batch_size

        emit_event(
            logger,
            "chunk_persistence_batch_plan",
            total_chunks=total_chunks,
            total_batches=total_batches,
            batch_size=batch_size,
        )

        for i in range(0, total_chunks, batch_size):
            batch = normalized_chunks[i : i + batch_size]
            current_batch_index = (i // batch_size) + 1

            try:
                await client.table("content_chunks").insert(batch).execute()

                # Calculate progress
                progress_pct = int((min(i + batch_size, total_chunks) / total_chunks) * 100)
                emit_event(
                    logger,
                    "chunk_persistence_batch_done",
                    batch_index=current_batch_index,
                    total_batches=total_batches,
                    progress_pct=progress_pct,
                )
                if inter_batch_sleep > 0:
                    import asyncio

                    await asyncio.sleep(inter_batch_sleep)

            except Exception as e:
                emit_event(
                    logger,
                    "chunk_persistence_batch_failed",
                    level="error",
                    batch_index=current_batch_index,
                    total_batches=total_batches,
                    error=compact_error(e),
                )
                raise e

        emit_event(logger, "chunk_persistence_completed", persisted_chunks=total_chunks)
        return total_chunks
