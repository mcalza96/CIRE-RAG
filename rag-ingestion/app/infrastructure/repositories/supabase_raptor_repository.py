import logging
from app.domain.repositories.raptor_repository import IRaptorRepository
from app.domain.raptor_schemas import SummaryNode

logger = logging.getLogger(__name__)

class SupabaseRaptorRepository(IRaptorRepository):
    """
    Supabase-backed implementation of IRaptorRepository.
    """

    def __init__(self, supabase_client=None):
        # Allow optional injection for testing, otherwise lazy load
        self.supabase = supabase_client
        
    async def get_client(self):
        if self.supabase is None:
            from app.infrastructure.supabase.client import get_async_supabase_client
            self.supabase = await get_async_supabase_client()
        return self.supabase

    async def save_summary_node(self, node: SummaryNode) -> None:
        """Persist a summary node to Supabase."""
        data = {
            "id": str(node.id),
            "tenant_id": str(node.tenant_id),
            "node_type": "Concepto",
            "title": node.title,
            "content": node.content,
            "embedding": node.embedding,
            "level": node.level,
            "collection_id": str(node.collection_id) if node.collection_id else None,
            "children_ids": [str(cid) for cid in node.children_ids],
            "properties": {
                "raptor_level": node.level,
                "is_summary": True,
                "children_count": len(node.children_ids)
            }
        }
        
        if node.source_document_id:
            data["source_document_id"] = str(node.source_document_id)
        
        try:
            client = await self.get_client()
            await client.table("regulatory_nodes").upsert(data).execute()
            logger.debug(f"Persisted summary node {node.id} at level {node.level}")
        except Exception as e:
            logger.error(f"Failed to persist summary node {node.id}: {e}")
            raise

    async def backfill_collection_id(self, source_document_id: str, collection_id: str) -> None:
        if not source_document_id or not collection_id:
            return

        client = await self.get_client()
        await (
            client.table("regulatory_nodes")
            .update({"collection_id": str(collection_id)})
            .eq("source_document_id", str(source_document_id))
            .execute()
        )
