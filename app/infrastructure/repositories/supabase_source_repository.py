from typing import Dict, Any, Optional
from app.domain.repositories.source_repository import ISourceRepository
from app.core.observability.context_vars import get_tenant_id
from app.infrastructure.supabase.client import get_async_supabase_client
from app.domain.schemas import SourceDocument

class SupabaseSourceRepository(ISourceRepository):
    @staticmethod
    def _tenant_from_context() -> str:
        return str(get_tenant_id() or "").strip()

    def __init__(self):
        self._client = None

    async def get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client

    async def update_status(self, doc_id: str, status: str, error_message: Optional[str] = None) -> None:
        client = await self.get_client()
        data = {"status": status}
        if error_message is not None:
            data["error_message"] = error_message
        query = client.table("source_documents").update(data).eq("id", doc_id)
        tenant_id = self._tenant_from_context()
        if tenant_id:
            query = query.eq("institution_id", tenant_id)
        await query.execute()

    async def update_metadata(self, doc_id: str, metadata: Dict[str, Any]) -> None:
        client = await self.get_client()
        query = client.table("source_documents").update({"metadata": metadata}).eq("id", doc_id)
        tenant_id = self._tenant_from_context()
        if tenant_id:
            query = query.eq("institution_id", tenant_id)
        await query.execute()

    async def update_status_and_metadata(self, doc_id: str, status: str, metadata: Dict[str, Any]) -> None:
        """Atomic update of both status and metadata to prevent race conditions."""
        client = await self.get_client()
        query = client.table("source_documents").update({
            "status": status,
            "metadata": metadata
        }).eq("id", doc_id)
        tenant_id = self._tenant_from_context()
        if tenant_id:
            query = query.eq("institution_id", tenant_id)
        await query.execute()

    async def log_event(
        self,
        doc_id: str,
        message: str,
        status: str = "INFO",
        node_type: str = "SYSTEM",
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not doc_id:
            return
        client = await self.get_client()
        await client.table("ingestion_events").insert({
            "source_document_id": doc_id,
            "message": message,
            "status": status,
            "node_type": node_type,
            "tenant_id": tenant_id,
            "metadata": metadata or {},
        }).execute()

    async def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        client = await self.get_client()
        try:
            query = client.table("source_documents").select("*").eq("id", doc_id)
            tenant_id = self._tenant_from_context()
            if tenant_id:
                query = query.eq("institution_id", tenant_id)
            res = await query.maybe_single().execute()
            return res.data
        except Exception:
            return None

    async def delete_document(self, doc_id: str) -> None:
        client = await self.get_client()
        query = client.table("source_documents").delete().eq("id", doc_id)
        tenant_id = self._tenant_from_context()
        if tenant_id:
            query = query.eq("institution_id", tenant_id)
        await query.execute()

    async def create_source_document(self, doc: SourceDocument) -> SourceDocument:
        client = await self.get_client()
        
        payload = {
            "filename": doc.filename,
            "metadata": doc.metadata,
            "status": doc.metadata.get("status", "pending")
        }

        tenant_id = (doc.metadata or {}).get("institution_id") or (doc.metadata or {}).get("tenant_id")
        if tenant_id:
            payload["institution_id"] = str(tenant_id)
        
        if doc.id:
            payload["id"] = str(doc.id)
            
        if doc.courseId:
            payload["course_id"] = str(doc.courseId)

        try:
            await client.table("source_documents").upsert(payload).execute()
        except Exception as e:
            raise e
        return doc
