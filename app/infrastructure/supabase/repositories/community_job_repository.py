import structlog
from typing import Dict, Any, Optional
from uuid import UUID
from app.infrastructure.supabase.client import get_async_supabase_client

logger = structlog.get_logger(__name__)

class CommunityJobRepository:
    """
    Handles database operations for community rebuild jobs.
    """
    async def get_latest_document_id(self, tenant_id: str) -> Optional[str]:
        supabase = await get_async_supabase_client()
        try:
            res = await supabase.table("source_documents").select("id").eq(
                "institution_id", tenant_id
            ).order("created_at", desc=True).limit(1).execute()
            rows = res.data or []
            return rows[0].get("id") if rows else None
        except Exception as exc:
            logger.warning("failed_to_get_latest_doc", tenant_id=tenant_id, error=str(exc))
            return None

    async def create_ingestion_event(self, doc_id: str, tenant_id: str, message: str, status: str, result: Dict[str, Any]):
        supabase = await get_async_supabase_client()
        try:
            await supabase.table("ingestion_events").insert({
                "source_document_id": doc_id,
                "tenant_id": tenant_id,
                "message": message,
                "status": status,
                "node_type": "SYSTEM",
                "metadata": {
                    "phase": "community_rebuild",
                    "result": result,
                },
            }).execute()
        except Exception as exc:
            logger.warning("failed_to_create_ingestion_event", doc_id=doc_id, error=str(exc))
