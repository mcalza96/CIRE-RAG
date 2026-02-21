import uuid
from typing import Any, Dict, List, Optional
from app.infrastructure.supabase.client import get_async_supabase_client
from app.domain.schemas.ingestion_schemas import IngestionMetadata
from app.domain.ingestion.types import IngestionStatus
from app.infrastructure.observability.correlation import get_correlation_id
from app.infrastructure.supabase.repositories.supabase_content_repository import SupabaseContentRepository
import structlog

logger = structlog.get_logger(__name__)

class TaxonomyRepository:
    """
    Manages the flattened taxonomy relationships for documents.
    Handles inserting into 'source_documents' and 'document_taxonomy'.
    """

    def __init__(self):
        self._supabase = None

    async def get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def register_document(
        self,
        filename: str,
        metadata: IngestionMetadata,
        course_id: Optional[str] = None,
        initial_status: IngestionStatus = IngestionStatus.READY,
    ) -> str:
        """
        Registers a document and its taxonomy relations.
        Returns the new document ID.
        """
        # 0. Cleanup Previous Versions (Prevent Duplicates)
        await self._cleanup_previous_versions(filename)

        supabase = await self.get_client()
        
        # 1. Create source_document entry
        # "title" is NOT a column in source_documents, it belongs in metadata.
        # Use mode="json" to ensure UUIDs and Enums are serialized to strings
        final_meta = metadata.model_dump(
            mode="json",
            by_alias=True, 
            exclude={"level_ids", "context_id", "subject_id", "type_id", "document_type_id"}
        )
        final_meta["title"] = metadata.title # Ensure title is in metadata
        final_meta["correlation_id"] = get_correlation_id()
        
        # Flatten extra metadata into top-level for agnostic retrieval
        if metadata.metadata:
            # We explicitly exclude keys that might collision with reserved fields if necessary, 
            # but for now we trust the source or let them override.
            # We iterate to avoid overwriting critical keys if we wanted to be safe, 
            # but a simple update is usually what we want (override defaults).
            final_meta.update(metadata.metadata)

        tenant_id: Optional[str] = str(metadata.institution_id) if metadata.institution_id else None
        if tenant_id:
            tenant_name_hint = None
            if isinstance(metadata.metadata, dict):
                tenant_name_hint = metadata.metadata.get("tenant_name")
            await self.ensure_institution_exists(tenant_id=tenant_id, institution_name=str(tenant_name_hint) if tenant_name_hint else None)

        collection_key, collection_name = self._extract_collection_fields(metadata)
        collection_id = None
        if tenant_id and collection_key:
            collection = await self.ensure_collection_open(
                tenant_id=tenant_id,
                collection_key=collection_key,
                collection_name=collection_name,
            )
            collection_id = collection.get("id")

        if collection_key:
            final_meta.setdefault("metadata", {})
            if isinstance(final_meta["metadata"], dict):
                final_meta["metadata"].setdefault("collection_key", collection_key)
                if collection_name:
                    final_meta["metadata"].setdefault("collection_name", collection_name)

        doc_data = {
            "filename": filename,
            "metadata": final_meta,
            "status": initial_status.value,
            "is_global": metadata.is_global  # Persist critical flag to column
        }

        if tenant_id:
            doc_data["institution_id"] = tenant_id
        if collection_id:
            doc_data["collection_id"] = collection_id
        
        if course_id:
             doc_data["course_id"] = course_id
             
        # Insert Document
        res = await supabase.table("source_documents").insert(doc_data).execute()
        if not res.data:
            raise Exception("Failed to insert source_document")
            
        doc_id = res.data[0]["id"]
        
        # 2. Flatten and Insert Taxonomy Relations
        nodes_to_link = set()
        
        # Add single value nodes (check for None)
        if metadata.type_id: nodes_to_link.add(str(metadata.type_id))
        if metadata.context_id: nodes_to_link.add(str(metadata.context_id))
        if metadata.subject_id: nodes_to_link.add(str(metadata.subject_id))
        if metadata.document_type_id: nodes_to_link.add(str(metadata.document_type_id))

        # Add multiple value nodes (Levels)
        if metadata.level_ids:
            for level_id in metadata.level_ids:
                nodes_to_link.add(str(level_id))
            
        # Prepare batch insert
        relations = [
            {"document_id": doc_id, "node_id": node_id} 
            for node_id in nodes_to_link
        ]
        
        if relations:
            await supabase.table("document_taxonomy").insert(relations).execute()
            
        return doc_id

    @staticmethod
    def _slugify_collection_key(value: str) -> str:
        key = (value or "").strip().lower()
        allowed = [c if c.isalnum() else "-" for c in key]
        normalized = "".join(allowed)
        while "--" in normalized:
            normalized = normalized.replace("--", "-")
        return normalized.strip("-") or "default"

    def _extract_collection_fields(self, metadata: IngestionMetadata) -> tuple[Optional[str], Optional[str]]:
        raw_meta: Dict[str, Any] = metadata.metadata or {}
        collection_key = raw_meta.get("collection_key") or raw_meta.get("collection_id")
        collection_name = raw_meta.get("collection_name")

        if collection_key:
            key = self._slugify_collection_key(str(collection_key))
            return key, str(collection_name) if collection_name else key

        if collection_name:
            key = self._slugify_collection_key(str(collection_name))
            return key, str(collection_name)

        return None, None

    async def _resolve_or_create_collection(
        self,
        tenant_id: Optional[str],
        collection_key: Optional[str],
        collection_name: Optional[str],
        allow_create: bool = True,
    ) -> Optional[str]:
        if not tenant_id or not collection_key:
            return None

        supabase = await self.get_client()

        if not allow_create:
            try:
                existing = (
                    await supabase.table("collections")
                    .select("id")
                    .eq("tenant_id", tenant_id)
                    .eq("collection_key", collection_key)
                    .limit(1)
                    .execute()
                )
                rows = existing.data or []
                if rows and rows[0].get("id"):
                    return str(rows[0]["id"])
                return None
            except Exception as e:
                logger.error("collection_lookup_failed", tenant_id=tenant_id, collection_key=collection_key, error=str(e))
                return None

        payload = {
            "tenant_id": tenant_id,
            "collection_key": collection_key,
            "name": collection_name or collection_key,
        }

        try:
            res = await (
                supabase.table("collections")
                .upsert(payload, on_conflict="tenant_id,collection_key")
                .execute()
            )
            data = res.data or []
            if data and data[0].get("id"):
                return str(data[0]["id"])
        except Exception as e:
            logger.error("collection_upsert_failed", tenant_id=tenant_id, collection_key=collection_key, error=str(e))
            return None

        return None

    async def resolve_collection_by_key(self, tenant_id: str, collection_key: str) -> Optional[Dict[str, Any]]:
        supabase = await self.get_client()
        result = (
            await supabase.table("collections")
            .select("id,tenant_id,collection_key,name,status")
            .eq("tenant_id", tenant_id)
            .eq("collection_key", collection_key)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    async def ensure_collection_open(
        self,
        tenant_id: str,
        collection_key: str,
        collection_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = self._slugify_collection_key(collection_key)
        existing = await self.resolve_collection_by_key(tenant_id=tenant_id, collection_key=key)
        if existing:
            status = str(existing.get("status") or "open").lower()
            if status == "sealed":
                supabase = await self.get_client()
                await supabase.table("collections").update({"status": "open"}).eq("id", existing.get("id")).execute()
                status = "open"
            return {
                "id": str(existing.get("id")),
                "collection_key": key,
                "name": existing.get("name") or collection_name or key,
                "status": status,
            }

        collection_id = await self._resolve_or_create_collection(
            tenant_id=tenant_id,
            collection_key=key,
            collection_name=collection_name or key,
            allow_create=True,
        )
        if not collection_id:
            raise ValueError(f"Failed to resolve/create collection '{key}' for tenant {tenant_id}")

        return {
            "id": collection_id,
            "collection_key": key,
            "name": collection_name or key,
            "status": "open",
        }

    async def ensure_institution_exists(self, tenant_id: str, institution_name: Optional[str] = None) -> None:
        supabase = await self.get_client()
        existing = (
            await supabase.table("institutions")
            .select("id")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if rows:
            return

        payload = {
            "id": tenant_id,
            "name": institution_name or f"Tenant {tenant_id[:8]}",
        }
        await supabase.table("institutions").insert(payload).execute()

    async def resolve_strategy_slug(self, type_id: Optional[str]) -> str:
        """
        Resolves the taxonomy node slug to determine which IngestionStrategy to usage.
        """
        if not type_id:
            return "content" # Default

        supabase = await self.get_client()
        res = await supabase.table("taxonomies").select("slug").eq("id", type_id).single().execute()
        
        if res.data:
            slug = res.data["slug"]
            # Map known slugs to strategy keys if needed, or return slug directly
            # The Dispatcher expects 'rubric' or 'content'.
            if slug == "rubric": return "RUBRIC"
            
            
        return "CONTENT"

    async def _cleanup_previous_versions(self, filename: str) -> None:
        """
        Automatically deletes any previous versions of a document with the same filename.
        This includes deleting associated chunks to maintain data integrity and prevent RAG duplicates.
        """
        supabase = await self.get_client()
        try:
            # 1. Identify previous versions
            res = await supabase.table("source_documents").select("id").eq("filename", filename).execute()
            docs = res.data or []
            
            if not docs:
                return

            logger.info(f"[TaxonomyRepository] Found {len(docs)} previous versions of '{filename}'. Cleaning up...")
            
            content_repo = SupabaseContentRepository()
            for doc in docs:
                doc_id = doc['id']
                # 2. Delete chunks
                try:
                    await content_repo.delete_chunks_by_source_id(doc_id)
                except Exception as e:
                    logger.error(f"[TaxonomyRepository] Error deleting chunks for {doc_id}: {e}")
                
                # 3. Delete document record
                try:
                    await supabase.table("source_documents").delete().eq("id", doc_id).execute()
                    logger.info(f"[TaxonomyRepository] Deleted previous version {doc_id}")
                except Exception as e:
                    logger.error(f"[TaxonomyRepository] Error deleting source record {doc_id}: {e}")
                    
        except Exception as e:
            logger.error(f"[TaxonomyRepository] Cleanup of previous versions failed: {e}")

