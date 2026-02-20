import logging
from uuid import UUID, NAMESPACE_URL, uuid5
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
        section_node_id = str(node.section_node_id) if node.section_node_id else None
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
                "children_count": len(node.children_ids),
                "source_standard": node.source_standard,
                "section_ref": node.section_ref,
                "section_node_id": section_node_id,
                "children_summary_ids": [str(cid) for cid in node.children_summary_ids],
            },
        }

        if node.source_document_id:
            data["source_document_id"] = str(node.source_document_id)

        try:
            client = await self.get_client()
            await client.table("regulatory_nodes").upsert(data).execute()
            await self._mirror_summary_into_knowledge_graph(client=client, node=node)
            logger.debug(f"Persisted summary node {node.id} at level {node.level}")
        except Exception as e:
            logger.error(f"Failed to persist summary node {node.id}: {e}")
            raise

    @staticmethod
    def _edge_id(source_id: str, target_id: str, relation_type: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"raptor-edge:{source_id}:{target_id}:{relation_type}"))

    async def _mirror_summary_into_knowledge_graph(self, client, node: SummaryNode) -> None:
        """Mirror RAPTOR summaries into knowledge graph tables for graph retrieval."""
        summary_id = str(node.id)
        tenant_id = str(node.tenant_id)

        entity_row = {
            "id": summary_id,
            "tenant_id": tenant_id,
            "name": node.title,
            "type": "RAPTOR_SUMMARY",
            "description": node.content,
            "embedding": node.embedding,
            "metadata": {
                "raptor_level": node.level,
                "is_raptor_summary": True,
                "source_document_id": str(node.source_document_id)
                if node.source_document_id
                else None,
                "collection_id": str(node.collection_id) if node.collection_id else None,
                "source_standard": node.source_standard,
                "section_ref": node.section_ref,
                "section_node_id": str(node.section_node_id) if node.section_node_id else None,
            },
        }
        await client.table("knowledge_entities").upsert(entity_row, on_conflict="id").execute()

        relation_rows: list[dict] = []
        if node.section_node_id:
            relation_rows.append(
                {
                    "id": self._edge_id(str(node.section_node_id), summary_id, "HAS_SUMMARY"),
                    "tenant_id": tenant_id,
                    "source_entity_id": str(node.section_node_id),
                    "target_entity_id": summary_id,
                    "relation_type": "HAS_SUMMARY",
                    "description": "Section has RAPTOR summary",
                    "weight": 1,
                    "metadata": {"source": "raptor_structural"},
                }
            )

        for child_summary_id in node.children_summary_ids:
            relation_rows.append(
                {
                    "id": self._edge_id(summary_id, str(child_summary_id), "SUMMARIZES"),
                    "tenant_id": tenant_id,
                    "source_entity_id": summary_id,
                    "target_entity_id": str(child_summary_id),
                    "relation_type": "SUMMARIZES",
                    "description": "RAPTOR parent-child summary relation",
                    "weight": 1,
                    "metadata": {"source": "raptor_hierarchy"},
                }
            )

        if relation_rows:
            await (
                client.table("knowledge_relations")
                .upsert(relation_rows, on_conflict="id")
                .execute()
            )

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
