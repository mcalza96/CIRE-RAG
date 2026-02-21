from typing import Optional, Dict, Any
from uuid import UUID
from app.infrastructure.supabase.client import get_async_supabase_client

class JobRepository:
    """
    Infrastructure repository for managing the job_queue table.
    """
    
    async def enqueue_enrichment_job(
        self,
        doc_id: str,
        tenant_id: Optional[str],
        payload: Dict[str, Any]
    ) -> bool:
        client = await get_async_supabase_client()
        
        # Check for existing pending/processing jobs for this document
        existing = await (
            client.table("job_queue")
            .select("id")
            .eq("job_type", "enrich_document")
            .in_("status", ["pending", "processing"])
            .contains("payload", {"source_document_id": str(doc_id)})
            .limit(1)
            .execute()
        )
        
        if existing.data:
            return False

        await (
            client.table("job_queue")
            .insert(
                {
                    "job_type": "enrich_document",
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "payload": payload,
                }
            )
            .execute()
        )
        return True
