from __future__ import annotations

from typing import Any, Dict, Optional

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.infrastructure.settings import settings
from app.api.v1.auth import require_service_auth
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.api.v1.tenant_guard import require_tenant_from_context
from app.infrastructure.supabase.client import get_async_supabase_client
from app.infrastructure.supabase.repositories.supabase_content_repository import SupabaseContentRepository

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/collections", tags=["collections"], dependencies=[Depends(require_service_auth)])


class CollectionDeleteResponse(BaseModel):
    status: str = Field(examples=["deleted"])
    collection_id: str
    documents_deleted: int
    chunks_deleted: int
    graph_artifacts_deleted: int
    raptor_nodes_deleted: int


@router.delete(
    "/{collection_id}",
    operation_id="deleteCollection",
    summary="Deep-delete a collection",
    description=(
        "Deletes a collection and ALL derived artifacts: "
        "source documents, content chunks, graph entities/relations/provenance, "
        "regulatory nodes/edges (RAPTOR), ingestion batches, and the collection record itself."
    ),
    response_model=CollectionDeleteResponse,
    responses={
        200: {
            "description": "Collection and all artifacts deleted",
            "content": {
                "application/json": {
                    "example": {
                        "status": "deleted",
                        "collection_id": "9a130ffb-b397-475b-a267-a1cc048b6d08",
                        "documents_deleted": 3,
                        "chunks_deleted": 78,
                        "graph_artifacts_deleted": 120,
                        "raptor_nodes_deleted": 2,
                    }
                }
            },
        },
        401: ERROR_RESPONSES[401],
        404: ERROR_RESPONSES[404],
        500: ERROR_RESPONSES[500],
    },
)
async def delete_collection(collection_id: str) -> CollectionDeleteResponse:
    try:
        require_tenant_from_context()
        sb = await get_async_supabase_client()

        # Verify collection exists
        col_row = await sb.table("collections").select("id").eq("id", collection_id).execute()
        if not (col_row.data or []):
            raise ApiError(
                status_code=404,
                code="COLLECTION_NOT_FOUND",
                message="Collection not found",
                details={"collection_id": collection_id},
            )

        # 1. Get all chunk IDs in this collection
        chunk_rows = await sb.table("content_chunks").select("id").eq("collection_id", collection_id).execute()
        chunk_ids = [r["id"] for r in (chunk_rows.data or [])]
        chunks_deleted = len(chunk_ids)

        # 2. Delete graph provenance for these chunks
        graph_deleted = 0
        if chunk_ids:
            for batch_start in range(0, len(chunk_ids), 100):
                batch = chunk_ids[batch_start : batch_start + 100]
                await sb.table("knowledge_node_provenance").delete().in_("chunk_id", batch).execute()
                graph_deleted += len(batch)

        # 3. Clean orphaned graph entities (no remaining provenance)
        await _cleanup_orphaned_graph_entities()

        # 4. Delete RAPTOR nodes/edges for this collection
        raptor_rows = await sb.table("regulatory_nodes").select("id").eq("collection_id", collection_id).execute()
        raptor_ids = [r["id"] for r in (raptor_rows.data or [])]
        raptor_deleted = len(raptor_ids)
        if raptor_ids:
            for batch_start in range(0, len(raptor_ids), 100):
                batch = raptor_ids[batch_start : batch_start + 100]
                await sb.table("regulatory_edges").delete().in_("source_id", batch).execute()
                await sb.table("regulatory_edges").delete().in_("target_id", batch).execute()
            await sb.table("regulatory_nodes").delete().eq("collection_id", collection_id).execute()

        # 5. Delete content chunks
        if chunk_ids:
            await sb.table("content_chunks").delete().eq("collection_id", collection_id).execute()

        # 6. Delete source documents
        doc_rows = await sb.table("source_documents").select("id").eq("collection_id", collection_id).execute()
        docs_deleted = len(doc_rows.data or [])
        if docs_deleted:
            await sb.table("source_documents").delete().eq("collection_id", collection_id).execute()

        # 7. Delete ingestion batches
        await sb.table("ingestion_batches").delete().eq("collection_id", collection_id).execute()

        # 8. Delete the collection itself
        await sb.table("collections").delete().eq("id", collection_id).execute()

        logger.info(
            "collection_deep_deleted",
            collection_id=collection_id,
            documents=docs_deleted,
            chunks=chunks_deleted,
            graph=graph_deleted,
            raptor=raptor_deleted,
        )

        return CollectionDeleteResponse(
            status="deleted",
            collection_id=collection_id,
            documents_deleted=docs_deleted,
            chunks_deleted=chunks_deleted,
            graph_artifacts_deleted=graph_deleted,
            raptor_nodes_deleted=raptor_deleted,
        )
    except ApiError:
        raise
    except Exception as e:
        logger.error("collection_delete_failed", collection_id=collection_id, error=str(e))
        raise ApiError(
            status_code=500,
            code="COLLECTION_DELETE_FAILED",
            message="Failed to delete collection",
        )


async def _cleanup_orphaned_graph_entities() -> None:
    """Delete entities with no provenance rows, then dangling relations."""
    try:
        # Use raw SQL for a clean NOT EXISTS subquery
        from app.infrastructure.supabase.client import get_async_supabase_client as _sb

        client = await _sb()
        # Delete relations pointing to entities that have no provenance
        await client.postgrest.rpc(
            "exec_sql",
            {
                "query": (
                    "DELETE FROM knowledge_relations "
                    "WHERE source_entity_id NOT IN (SELECT DISTINCT entity_id FROM knowledge_node_provenance) "
                    "OR target_entity_id NOT IN (SELECT DISTINCT entity_id FROM knowledge_node_provenance)"
                )
            },
        ).execute()
        # Delete orphan entities
        await client.postgrest.rpc(
            "exec_sql",
            {
                "query": (
                    "DELETE FROM knowledge_entities "
                    "WHERE id NOT IN (SELECT DISTINCT entity_id FROM knowledge_node_provenance)"
                )
            },
        ).execute()
    except Exception as e:
        # Non-critical: orphans will be cleaned on next collection delete
        logger.warning("orphan_entity_cleanup_skipped", error=str(e))
