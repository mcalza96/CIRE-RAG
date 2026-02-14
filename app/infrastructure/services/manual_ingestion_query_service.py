from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.core.observability.context_vars import get_tenant_id
from app.infrastructure.supabase.client import get_async_supabase_client


logger = logging.getLogger(__name__)


class ManualIngestionQueryService:
    @staticmethod
    def _tenant_from_context() -> str:
        return str(get_tenant_id() or "").strip()

    @classmethod
    def _enforce_tenant_match(cls, tenant_id: str, location: str) -> str:
        tenant_req = str(tenant_id or "").strip()
        tenant_ctx = cls._tenant_from_context()
        if tenant_ctx and tenant_req and tenant_ctx != tenant_req:
            raise ValueError(f"TENANT_MISMATCH:{location}")
        return tenant_req or tenant_ctx

    async def count_pending_documents(self, tenant_id: str, limit: int, statuses: List[str]) -> int:
        tenant_scoped = self._enforce_tenant_match(tenant_id, "count_pending_documents")
        client = await get_async_supabase_client()
        response = (
            await client.table("source_documents")
            .select("id")
            .eq("institution_id", str(tenant_scoped))
            .in_("status", statuses)
            .limit(int(limit))
            .execute()
        )
        return len(response.data or [])

    async def list_recent_documents(self, limit: int = 20) -> List[Dict[str, Any]]:
        tenant_ctx = self._tenant_from_context()
        if not tenant_ctx:
            raise ValueError("TENANT_CONTEXT_REQUIRED")
        client = await get_async_supabase_client()
        response = (
            await client.table("source_documents")
            .select("*")
            .eq("institution_id", tenant_ctx)
            .order("created_at", desc=True)
            .limit(int(limit))
            .execute()
        )
        return response.data or []

    async def list_collections(self, tenant_id: str) -> List[Dict[str, Any]]:
        tenant_scoped = self._enforce_tenant_match(tenant_id, "list_collections")
        client = await get_async_supabase_client()
        response = (
            await client.table("collections")
            .select("id,tenant_id,collection_key,name,status,created_at")
            .eq("tenant_id", str(tenant_scoped))
            .order("created_at", desc=False)
            .execute()
        )
        return response.data or []

    async def list_tenants(self, limit: int = 200) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        capped = max(1, min(int(limit or 200), 1000))

        try:
            response = (
                await client.table("institutions")
                .select("id,name,created_at")
                .order("name", desc=False)
                .limit(capped)
                .execute()
            )
            rows = response.data or []
            if rows:
                return rows
        except Exception:
            logger.exception("list_tenants_from_institutions_failed")

        # Fallback for environments without institutions rows.
        response = (
            await client.table("source_documents")
            .select("institution_id,created_at")
            .order("created_at", desc=True)
            .limit(capped * 3)
            .execute()
        )
        rows = response.data or []
        unique: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in rows:
            tenant_id = str((item or {}).get("institution_id") or "").strip()
            if not tenant_id or tenant_id in seen:
                continue
            seen.add(tenant_id)
            unique.append({"id": tenant_id, "name": tenant_id, "created_at": item.get("created_at")})
            if len(unique) >= capped:
                break
        return unique

    async def cleanup_collection(self, tenant_id: str, collection_key: str) -> Dict[str, Any]:
        tenant = self._enforce_tenant_match(tenant_id, "cleanup_collection")
        key = str(collection_key or "").strip().lower()
        if not tenant or not key:
            raise ValueError("INVALID_COLLECTION_SCOPE")

        client = await get_async_supabase_client()
        collection_res = (
            await client.table("collections")
            .select("id,tenant_id,collection_key,status")
            .eq("tenant_id", tenant)
            .eq("collection_key", key)
            .maybe_single()
            .execute()
        )
        collection = collection_res.data
        if not collection:
            raise ValueError("COLLECTION_NOT_FOUND")

        collection_id = str(collection["id"])

        docs_res = (
            await client.table("source_documents")
            .select("id")
            .eq("collection_id", collection_id)
            .execute()
        )
        doc_count = len(docs_res.data or [])

        nodes_res = (
            await client.table("regulatory_nodes")
            .select("id")
            .eq("collection_id", collection_id)
            .execute()
        )
        regulatory_nodes_count = len(nodes_res.data or [])

        batches_res = (
            await client.table("ingestion_batches")
            .select("id")
            .eq("collection_id", collection_id)
            .execute()
        )
        batches_count = len(batches_res.data or [])

        await client.table("regulatory_nodes").delete().eq("collection_id", collection_id).execute()
        await client.table("source_documents").delete().eq("collection_id", collection_id).execute()
        await client.table("ingestion_batches").delete().eq("collection_id", collection_id).execute()

        return {
            "status": "cleaned",
            "tenant_id": tenant,
            "collection_id": collection_id,
            "collection_key": key,
            "deleted": {
                "source_documents": doc_count,
                "regulatory_nodes": regulatory_nodes_count,
                "ingestion_batches": batches_count,
            },
        }

    async def create_batch(
        self,
        tenant_id: str,
        collection_id: str,
        collection_key: str,
        collection_name: str,
        total_files: int,
        auto_seal: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tenant_scoped = self._enforce_tenant_match(tenant_id, "create_batch")
        client = await get_async_supabase_client()

        payload = {
            "tenant_id": str(tenant_scoped),
            "collection_id": str(collection_id),
            "total_files": int(total_files),
            "status": "pending",
            "auto_seal": bool(auto_seal),
            "metadata": {
                "collection_key": collection_key,
                "collection_name": collection_name,
                **(metadata or {}),
            },
        }

        result = await client.table("ingestion_batches").insert(payload).execute()
        row = (result.data or [{}])[0]
        return {
            "batch_id": row.get("id"),
            "tenant_id": payload["tenant_id"],
            "collection_id": payload["collection_id"],
            "collection_key": collection_key,
            "status": row.get("status") or "pending",
            "total_files": payload["total_files"],
            "auto_seal": payload["auto_seal"],
        }

    async def get_batch_upload_context(self, batch_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()

        batch_res = (
            await client.table("ingestion_batches")
            .select("id,tenant_id,collection_id,total_files,auto_seal,status")
            .eq("id", str(batch_id))
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not isinstance(batch, dict):
            raise ValueError("BATCH_NOT_FOUND")
        tenant_ctx = self._tenant_from_context()
        if tenant_ctx and str(batch.get("tenant_id") or "") != tenant_ctx:
            raise ValueError("TENANT_MISMATCH:get_batch_upload_context")

        collection_id = batch.get("collection_id")
        if not collection_id:
            raise ValueError("BATCH_MISSING_COLLECTION")

        collection_res = (
            await client.table("collections")
            .select("id,tenant_id,collection_key,name,status")
            .eq("id", str(collection_id))
            .maybe_single()
            .execute()
        )
        collection = collection_res.data
        if not isinstance(collection, dict):
            raise ValueError("COLLECTION_NOT_FOUND")

        if str(collection.get("status", "open")).lower() == "sealed":
            await client.table("collections").update({"status": "open"}).eq("id", collection["id"]).execute()
            collection["status"] = "open"

        docs_res = (
            await client.table("source_documents")
            .select("id,filename,status")
            .eq("batch_id", str(batch_id))
            .execute()
        )
        docs = docs_res.data or []

        return {
            "batch": batch,
            "collection": collection,
            "documents": docs,
        }

    async def upload_to_storage(
        self,
        bucket: str,
        path: str,
        content_bytes: bytes,
        content_type: str,
    ) -> None:
        client = await get_async_supabase_client()
        storage = client.storage.from_(bucket)
        try:
            await storage.upload(
                path=path,
                file=content_bytes,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        except TypeError:
            await storage.upload(path, content_bytes, {"content-type": content_type, "upsert": "false"})

    async def queue_source_document_for_batch(self, payload: Dict[str, Any], batch_id: str) -> None:
        client = await get_async_supabase_client()
        await client.table("source_documents").insert(payload).execute()
        await client.table("ingestion_batches").update({"status": "processing"}).eq("id", str(batch_id)).execute()

    async def get_batch_for_seal(self, batch_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()
        batch_res = (
            await client.table("ingestion_batches")
            .select("id,tenant_id,collection_id,total_files,completed,failed,status")
            .eq("id", str(batch_id))
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not isinstance(batch, dict):
            raise ValueError("BATCH_NOT_FOUND")
        tenant_ctx = self._tenant_from_context()
        if tenant_ctx and str(batch.get("tenant_id") or "") != tenant_ctx:
            raise ValueError("TENANT_MISMATCH:get_batch_for_seal")
        return batch

    async def seal_collection(self, collection_id: str) -> None:
        if not collection_id:
            return
        client = await get_async_supabase_client()
        await client.table("collections").update({"status": "sealed"}).eq("id", str(collection_id)).execute()

    async def list_document_statuses_for_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        docs_res = (
            await client.table("source_documents")
            .select("status")
            .eq("batch_id", str(batch_id))
            .execute()
        )
        return docs_res.data or []

    async def update_batch_status_counters(self, batch_id: str, completed: int, failed: int, status: str) -> None:
        client = await get_async_supabase_client()
        await client.table("ingestion_batches").update(
            {
                "completed": int(completed),
                "failed": int(failed),
                "status": str(status),
            }
        ).eq("id", str(batch_id)).execute()

    async def get_batch_status_data(self, batch_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()

        batch_res = (
            await client.table("ingestion_batches")
            .select("id,tenant_id,collection_id,total_files,completed,failed,status,auto_seal,metadata,created_at,updated_at")
            .eq("id", str(batch_id))
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not isinstance(batch, dict):
            raise ValueError("BATCH_NOT_FOUND")
        tenant_ctx = self._tenant_from_context()
        if tenant_ctx and str(batch.get("tenant_id") or "") != tenant_ctx:
            raise ValueError("TENANT_MISMATCH:get_batch_status_data")

        docs_res = (
            await client.table("source_documents")
            .select("id,filename,status,created_at,batch_id,metadata")
            .eq("batch_id", str(batch_id))
            .order("created_at", desc=False)
            .execute()
        )
        docs = docs_res.data or []

        doc_ids = [str(d.get("id")) for d in docs if isinstance(d, dict) and d.get("id")]
        events: List[Dict[str, Any]] = []
        if doc_ids:
            events_res = (
                await client.table("ingestion_events")
                .select("source_document_id,message,status,created_at")
                .in_("source_document_id", doc_ids)
                .order("created_at", desc=True)
                .execute()
            )
            events = events_res.data or []

        return {
            "batch": batch,
            "documents": docs,
            "events": events,
        }

    async def upsert_source_document(
        self,
        document_id: str,
        filename: str,
        tenant_id: str,
        metadata: Dict[str, Any],
        collection_id: Optional[str] = None,
        course_id: Optional[str] = None,
    ) -> None:
        tenant_scoped = self._enforce_tenant_match(tenant_id, "upsert_source_document")
        client = await get_async_supabase_client()
        upsert_payload: Dict[str, Any] = {
            "id": str(document_id),
            "filename": str(filename),
            "status": str(metadata.get("status") or "queued"),
            "metadata": metadata,
            "institution_id": str(tenant_scoped),
        }
        if collection_id:
            upsert_payload["collection_id"] = str(collection_id)

        if course_id:
            try:
                upsert_payload["course_id"] = str(UUID(str(course_id)))
            except Exception:
                logger.warning("Ignoring invalid course_id in institutional metadata: %s", course_id)

        try:
            await client.table("source_documents").upsert(upsert_payload).execute()
        except Exception as exc:
            if "source_documents_course_id_fkey" in str(exc) and "course_id" in upsert_payload:
                logger.warning("course_id FK failed; retrying source_document upsert without course_id")
                upsert_payload.pop("course_id", None)
                await client.table("source_documents").upsert(upsert_payload).execute()
            else:
                raise
