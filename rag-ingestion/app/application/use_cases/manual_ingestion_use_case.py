import os
import json
import math
import shutil
import tempfile
import structlog
from typing import Optional, List, Dict, Any
from fastapi import UploadFile
from app.workflows.ingestion.dispatcher import IngestionDispatcher
from app.schemas.ingestion import IngestionMetadata
from app.services.database.taxonomy_manager import TaxonomyManager
from app.workflows.ingestion.mock_upload_file import MockUploadFile

logger = structlog.get_logger(__name__)

from app.domain.types.ingestion_status import IngestionStatus

from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository
from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.services.knowledge.raptor_processor import RaptorProcessor
from app.infrastructure.repositories.supabase_raptor_repository import SupabaseRaptorRepository
from app.domain.raptor_schemas import BaseChunk
from uuid import uuid4, UUID
from app.infrastructure.supabase.client import get_async_supabase_client
from app.core.settings import settings

class ManualIngestionUseCase:
    def __init__(self, dispatcher: IngestionDispatcher):
        self.dispatcher = dispatcher
        self.taxonomy_manager = TaxonomyManager()
        self.content_repo = SupabaseContentRepository()
        self.source_repo = SupabaseSourceRepository()
        # Lazy init for Raptor to avoid async issues in init if needed, 
        # but here we can just init repositories.
        # We need a client for RaptorRepo. 
        # Ideally we inject, but for now we'll do safe init inside methods or use singleton.
        self.raptor_processor = None 

    async def _get_pending_snapshot(self, tenant_id: Optional[str]) -> Dict[str, Optional[int]]:
        max_pending = int(getattr(settings, "INGESTION_MAX_PENDING_PER_TENANT", 0) or 0)
        if not tenant_id:
            return {
                "queue_depth": 0,
                "max_pending": max_pending if max_pending > 0 else None,
                "estimated_wait_seconds": 0,
            }

        limit = max_pending + 1 if max_pending > 0 else 1000
        client = await get_async_supabase_client()
        response = (
            await client.table("source_documents")
            .select("id")
            .eq("institution_id", str(tenant_id))
            .in_(
                "status",
                [
                    IngestionStatus.QUEUED.value,
                    IngestionStatus.PENDING.value,
                    IngestionStatus.PENDING_INGESTION.value,
                    IngestionStatus.PROCESSING.value,
                    IngestionStatus.PROCESSING_V2.value,
                ],
            )
            .limit(limit)
            .execute()
        )
        pending_count = len(response.data or [])
        docs_per_minute_per_worker = max(1, int(getattr(settings, "INGESTION_DOCS_PER_MINUTE_PER_WORKER", 2) or 2))
        worker_concurrency = max(1, int(getattr(settings, "WORKER_CONCURRENCY", 1) or 1))
        throughput_per_minute = docs_per_minute_per_worker * worker_concurrency
        estimated_wait_seconds = int(math.ceil((pending_count / throughput_per_minute) * 60)) if pending_count > 0 else 0

        return {
            "queue_depth": pending_count,
            "max_pending": max_pending if max_pending > 0 else None,
            "estimated_wait_seconds": estimated_wait_seconds,
        }

    async def _enforce_pending_limit(self, tenant_id: Optional[str]) -> Dict[str, Optional[int]]:
        if not tenant_id:
            return await self._get_pending_snapshot(tenant_id=tenant_id)

        snapshot = await self._get_pending_snapshot(tenant_id=tenant_id)
        max_pending = int(snapshot.get("max_pending") or 0)
        if max_pending <= 0:
            return snapshot

        pending_count = int(snapshot.get("queue_depth") or 0)
        if pending_count >= max_pending:
            raise ValueError(
                "INGESTION_BACKPRESSURE "
                f"tenant={tenant_id} pending={pending_count} max={max_pending} "
                f"eta_seconds={int(snapshot.get('estimated_wait_seconds') or 0)}"
            )

        return snapshot

    async def get_queue_status(self, tenant_id: str) -> Dict[str, Optional[int]]:
        tenant = str(tenant_id or "").strip()
        if not tenant:
            raise ValueError("INVALID_TENANT_ID")
        return await self._get_pending_snapshot(tenant_id=tenant)

    async def get_documents(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Lists recently registered documents from Supabase.
        """
        client = await get_async_supabase_client()
        res = await client.table("source_documents").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data

    async def list_collections(self, tenant_id: str) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        res = (
            await client.table("collections")
            .select("id,tenant_id,collection_key,name,status,created_at")
            .eq("tenant_id", str(tenant_id))
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []

    async def cleanup_collection(self, tenant_id: str, collection_key: str) -> Dict[str, Any]:
        tenant = str(tenant_id or "").strip()
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

    async def retry_ingestion(self, doc_id: str):
        """
        Re-triggers background processing for an existing document.
        Updates the existing record to QUEUED status and increments retry_count.
        """
        # 1. Fetch document record
        doc = await self.source_repo.get_by_id(doc_id)
        if not doc:
            raise ValueError(f"Document {doc_id} not found.")

        current_metadata = doc.get("metadata", {}) or {}
        
        # 2. Increment Retry Count
        retry_count = current_metadata.get("retry_count", 0)
        new_retry_count = retry_count + 1
        
        # 3. Update Metadata and Status
        current_metadata["retry_count"] = new_retry_count
        
        # We also need to ensure the file is actually there if it's a local file.
        # But for 'retry', we rely on the worker to validate existence or fail again.
        # This keeps the API response fast.
        
        logger.info(f"[ManualIngest] Retrying document {doc_id} (Attempt {new_retry_count})")
        
        await self.source_repo.update_status_and_metadata(
            doc_id, 
            IngestionStatus.QUEUED.value, 
            current_metadata
        )
        
        return doc_id

    async def execute(self, file: UploadFile, metadata: str):
        """
        Processes a manual document ingestion request.
        """
        # 1. Parse Metadata
        try:
            parsed_metadata = IngestionMetadata.parse_raw(metadata)
            logger.info(f"[ManualIngest] Received ingestion for: {file.filename}")
        except Exception as e:
            logger.error(f"[ManualIngest] Metadata parsing error: {e}")
            raise ValueError(f"Invalid metadata: {e}")

        tenant_id = str(parsed_metadata.institution_id) if parsed_metadata.institution_id else None
        extra_meta = parsed_metadata.metadata or {}
        collection_key_raw = extra_meta.get("collection_key") or extra_meta.get("collection_id")
        collection_name_raw = extra_meta.get("collection_name")
        if tenant_id and collection_key_raw:
            await self.taxonomy_manager.ensure_collection_open(
                tenant_id=tenant_id,
                collection_key=str(collection_key_raw),
                collection_name=str(collection_name_raw) if collection_name_raw else None,
            )

        # 2. Save to Temp Location
        # We must save it because background tasks might access it after the request closes
        temp_dir = tempfile.gettempdir()
        # FIX: Use unique filename to prevent collisions in concurrent uploads
        unique_id = uuid4()
        safe_filename = f"ingest_{unique_id}_{file.filename}" 
        file_path = os.path.join(temp_dir, safe_filename)
        
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            logger.error(f"[ManualIngest] Error saving temp file: {e}")
            raise RuntimeError("Could not save uploaded file locally.")

        # 3. Return file + metadata for queue registration.
        original_filename = file.filename or safe_filename
        return file_path, original_filename, parsed_metadata

    async def process_background(self, file_path: str, original_filename: str, metadata: IngestionMetadata) -> Dict[str, Any]:
        """
        Registers document for async Worker processing.
        Does NOT execute ingestion locally.
        """
        try:
            tenant_id = str(metadata.institution_id) if metadata.institution_id else None
            await self._enforce_pending_limit(tenant_id=tenant_id)

            # 1. Enrich Metadata with storage path for the worker
            # Use top-level field now supported by schema
            metadata.storage_path = file_path
            
            # 2. Register Document (Set to QUEUED to trigger Worker)
            logger.info(f"[ManualIngest] Registering document {original_filename} via Worker (Path: {file_path})")
            doc_id = await self.taxonomy_manager.register_document(
                filename=original_filename,
                metadata=metadata,
                initial_status=IngestionStatus.QUEUED  # Use QUEUED to trigger policy
            )
            
            logger.info(f"[ManualIngest] Document {doc_id} queued successfully. Worker should pick it up.")
            
            # NOTE: We do NOT delete the temp file here. The worker needs it.
            # The Worker (ProcessDocumentWorkerUseCase) is responsible for deletion after processing.
            
            queue = await self._get_pending_snapshot(tenant_id=tenant_id)
            return {"document_id": str(doc_id), "queue": queue}

        except Exception as e:
            logger.error(f"[ManualIngest] Failed to register document {original_filename}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            raise e

    async def create_batch(
        self,
        tenant_id: str,
        collection_key: str,
        total_files: int,
        auto_seal: bool = False,
        collection_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if total_files <= 0:
            raise ValueError("INVALID_TOTAL_FILES")

        tenant_name = None
        if isinstance(metadata, dict):
            tenant_name = metadata.get("tenant_name")
        await self.taxonomy_manager.ensure_institution_exists(
            tenant_id=str(tenant_id),
            institution_name=str(tenant_name) if tenant_name else None,
        )

        collection = await self.taxonomy_manager.ensure_collection_open(
            tenant_id=str(tenant_id),
            collection_key=str(collection_key),
            collection_name=collection_name,
        )

        client = await get_async_supabase_client()
        payload = {
            "tenant_id": str(tenant_id),
            "collection_id": str(collection["id"]),
            "total_files": int(total_files),
            "status": "pending",
            "auto_seal": bool(auto_seal),
            "metadata": {
                "collection_key": collection["collection_key"],
                "collection_name": collection.get("name") or collection["collection_key"],
                **(metadata or {}),
            },
        }
        result = await client.table("ingestion_batches").insert(payload).execute()
        row = (result.data or [{}])[0]
        return {
            "batch_id": row.get("id"),
            "tenant_id": payload["tenant_id"],
            "collection_id": payload["collection_id"],
            "collection_key": collection["collection_key"],
            "status": row.get("status") or "pending",
            "total_files": payload["total_files"],
            "auto_seal": payload["auto_seal"],
        }

    async def add_file_to_batch(
        self,
        batch_id: str,
        file: UploadFile,
        metadata: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = await get_async_supabase_client()

        batch_res = (
            await client.table("ingestion_batches")
            .select("id,tenant_id,collection_id,total_files,auto_seal,status")
            .eq("id", batch_id)
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not batch:
            raise ValueError("BATCH_NOT_FOUND")

        if not batch.get("collection_id"):
            raise ValueError("BATCH_MISSING_COLLECTION")

        collection_res = (
            await client.table("collections")
            .select("id,tenant_id,collection_key,name,status")
            .eq("id", batch["collection_id"])
            .maybe_single()
            .execute()
        )
        collection = collection_res.data
        if not collection:
            raise ValueError("COLLECTION_NOT_FOUND")
        if str(collection.get("status", "open")).lower() == "sealed":
            await client.table("collections").update({"status": "open"}).eq("id", collection["id"]).execute()

        docs_res = (
            await client.table("source_documents")
            .select("id,filename,status")
            .eq("batch_id", batch_id)
            .execute()
        )
        docs = docs_res.data or []
        for doc in docs:
            if str(doc.get("filename")) == str(file.filename):
                queue = await self._get_pending_snapshot(tenant_id=str(batch["tenant_id"]))
                return {
                    "doc_id": doc.get("id"),
                    "filename": doc.get("filename"),
                    "status": doc.get("status"),
                    "queued": False,
                    "idempotent": True,
                    "queue": queue,
                }

        if len(docs) >= int(batch.get("total_files") or 0):
            raise ValueError("BATCH_FILE_LIMIT_EXCEEDED")

        await self._enforce_pending_limit(tenant_id=str(batch["tenant_id"]))

        parsed_metadata: Dict[str, Any] = {}
        if metadata:
            try:
                parsed_metadata = json.loads(metadata)
            except Exception as e:
                raise ValueError(f"INVALID_METADATA_JSON: {e}")

        safe_filename = file.filename or f"document_{uuid4()}.bin"
        doc_id = str(uuid4())
        storage_path = f"{batch['tenant_id']}/{collection['collection_key']}/{batch_id}/{doc_id}_{safe_filename}"

        content_bytes = await file.read()
        if not content_bytes:
            raise ValueError("EMPTY_FILE")

        target_bucket = settings.RAG_STORAGE_BUCKET
        storage = client.storage.from_(target_bucket)
        content_type = file.content_type or "application/octet-stream"
        try:
            await storage.upload(path=storage_path, file=content_bytes, file_options={"content-type": content_type, "upsert": "false"})
        except TypeError:
            await storage.upload(storage_path, content_bytes, {"content-type": content_type, "upsert": "false"})

        merged_metadata = {
            "status": IngestionStatus.QUEUED.value,
            "institution_id": str(batch["tenant_id"]),
            "is_global": False,
            "storage_path": storage_path,
            "storage_bucket": target_bucket,
            "batch_id": str(batch_id),
            "collection_id": str(collection["id"]),
            "collection_key": str(collection["collection_key"]),
            "collection_name": str(collection.get("name") or collection.get("collection_key")),
            **parsed_metadata,
        }
        nested_meta = merged_metadata.get("metadata")
        nested: Dict[str, Any] = nested_meta if isinstance(nested_meta, dict) else {}
        nested.setdefault("collection_id", str(collection["id"]))
        nested.setdefault("collection_key", str(collection["collection_key"]))
        nested.setdefault("collection_name", str(collection.get("name") or collection.get("collection_key")))
        merged_metadata["metadata"] = nested

        insert_payload = {
            "id": doc_id,
            "filename": safe_filename,
            "status": IngestionStatus.QUEUED.value,
            "metadata": merged_metadata,
            "institution_id": str(batch["tenant_id"]),
            "collection_id": str(collection["id"]),
            "batch_id": str(batch_id),
        }
        await client.table("source_documents").insert(insert_payload).execute()

        await client.table("ingestion_batches").update({"status": "processing"}).eq("id", batch_id).execute()

        queue = await self._get_pending_snapshot(tenant_id=str(batch["tenant_id"]))

        return {
            "doc_id": doc_id,
            "filename": safe_filename,
            "status": IngestionStatus.QUEUED.value,
            "queued": True,
            "idempotent": False,
            "queue": queue,
        }

    async def seal_batch(self, batch_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()

        batch_res = (
            await client.table("ingestion_batches")
            .select("id,collection_id,total_files,completed,failed,status")
            .eq("id", batch_id)
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not batch:
            raise ValueError("BATCH_NOT_FOUND")

        collection_id = batch.get("collection_id")
        if collection_id:
            await client.table("collections").update({"status": "sealed"}).eq("id", collection_id).execute()

        docs_res = (
            await client.table("source_documents")
            .select("status")
            .eq("batch_id", batch_id)
            .execute()
        )
        docs = docs_res.data or []
        success_states = {"success", "processed", "completed", "ready"}
        failed_states = {"failed", "error", "dead_letter"}

        completed = 0
        failed = 0
        for row in docs:
            st = str(row.get("status") or "").lower()
            if st in success_states:
                completed += 1
            elif st in failed_states:
                failed += 1

        total_files = int(batch.get("total_files") or len(docs))
        if total_files <= 0:
            total_files = len(docs)

        if completed + failed >= total_files and total_files > 0:
            if failed == 0:
                status = "completed"
            elif completed == 0:
                status = "failed"
            else:
                status = "partial"
        else:
            status = "processing"

        await client.table("ingestion_batches").update(
            {
                "completed": completed,
                "failed": failed,
                "status": status,
            }
        ).eq("id", batch_id).execute()

        return {
            "batch_id": batch_id,
            "status": status,
            "completed": completed,
            "failed": failed,
            "total_files": total_files,
            "sealed": True,
        }

    async def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        client = await get_async_supabase_client()
        batch_res = (
            await client.table("ingestion_batches")
            .select("id,tenant_id,collection_id,total_files,completed,failed,status,auto_seal,metadata,created_at,updated_at")
            .eq("id", batch_id)
            .maybe_single()
            .execute()
        )
        batch = batch_res.data
        if not batch:
            raise ValueError("BATCH_NOT_FOUND")

        docs_res = (
            await client.table("source_documents")
            .select("id,filename,status,created_at,batch_id,metadata")
            .eq("batch_id", batch_id)
            .order("created_at", desc=False)
            .execute()
        )

        docs = docs_res.data or []
        doc_ids = [str(d.get("id")) for d in docs if isinstance(d, dict) and d.get("id")]

        latest_event_by_doc: Dict[str, Dict[str, Any]] = {}
        if doc_ids:
            events_res = (
                await client.table("ingestion_events")
                .select("source_document_id,message,status,created_at")
                .in_("source_document_id", doc_ids)
                .order("created_at", desc=True)
                .execute()
            )
            for ev in events_res.data or []:
                if not isinstance(ev, dict):
                    continue
                source_id = str(ev.get("source_document_id") or "")
                if not source_id or source_id in latest_event_by_doc:
                    continue
                latest_event_by_doc[source_id] = ev

        stage_counts: Dict[str, int] = {}
        sample_stage_refs: list[Dict[str, Any]] = []

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or "")
            latest_event = latest_event_by_doc.get(doc_id)
            if not latest_event:
                continue

            message = str(latest_event.get("message") or "")
            stage = self._infer_worker_stage(message=message)
            doc["worker_stage"] = stage
            doc["worker_last_message"] = message

            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if len(sample_stage_refs) < 8:
                sample_stage_refs.append(
                    {
                        "doc_id": doc_id,
                        "filename": str(doc.get("filename") or ""),
                        "stage": stage,
                        "message": message,
                    }
                )
        attempted = 0
        stitched = 0
        degraded_inline = 0
        parse_failed = 0
        parse_failed_copyright = 0
        skipped = 0
        docs_with_visual = 0
        docs_with_loss = 0
        copyright_refs: list[Dict[str, Any]] = []

        for doc in docs:
            metadata = doc.get("metadata") if isinstance(doc, dict) else None
            if not isinstance(metadata, dict):
                continue
            visual = metadata.get("visual_anchor")
            if not isinstance(visual, dict):
                continue

            docs_with_visual += 1
            doc_attempted = int(visual.get("attempted") or 0)
            doc_stitched = int(visual.get("stitched") or 0)
            doc_degraded = int(visual.get("degraded_inline") or 0)
            doc_parse_failed = int(visual.get("parse_failed") or 0)
            doc_parse_failed_copyright = int(visual.get("parse_failed_copyright") or 0)
            doc_skipped = int(visual.get("skipped") or 0)
            doc_refs = visual.get("parse_failed_copyright_refs")

            attempted += doc_attempted
            stitched += doc_stitched
            degraded_inline += doc_degraded
            parse_failed += doc_parse_failed
            parse_failed_copyright += doc_parse_failed_copyright
            skipped += doc_skipped

            if (doc_degraded + doc_parse_failed + doc_skipped) > 0:
                docs_with_loss += 1

            if isinstance(doc_refs, list) and doc_refs:
                for item in doc_refs:
                    if not isinstance(item, dict):
                        continue
                    image_name = str(item.get("image") or "")
                    if "/" in image_name:
                        image_name = image_name.rsplit("/", 1)[-1]
                    copyright_refs.append(
                        {
                            "doc_id": str(doc.get("id") or ""),
                            "filename": str(doc.get("filename") or ""),
                            "page": int(item.get("page") or 0),
                            "parent_chunk_id": str(item.get("parent_chunk_id") or ""),
                            "image": image_name,
                        }
                    )

        max_refs = 20

        visual_accounting = {
            "docs_with_visual": docs_with_visual,
            "docs_with_loss": docs_with_loss,
            "attempted": attempted,
            "stitched": stitched,
            "degraded_inline": degraded_inline,
            "parse_failed": parse_failed,
            "parse_failed_copyright": parse_failed_copyright,
            "skipped": skipped,
            "loss_events": degraded_inline + parse_failed + skipped,
            "copyright_refs_total": len(copyright_refs),
            "copyright_refs": copyright_refs[:max_refs],
        }

        worker_progress = {
            "stage_counts": stage_counts,
            "sample_refs": sample_stage_refs,
        }

        return {
            "batch": batch,
            "documents": docs,
            "visual_accounting": visual_accounting,
            "worker_progress": worker_progress,
        }

    @staticmethod
    def _infer_worker_stage(message: str) -> str:
        text = (message or "").lower()
        if "raptor" in text:
            return "RAPTOR"
        if "grafo" in text or "graph" in text:
            return "GRAPH"
        if "visual anchor" in text or "visual" in text:
            return "VISUAL"
        if "persist" in text:
            return "PERSIST"
        if "dispatch" in text or "ingestion" in text or "procesamiento" in text:
            return "INGEST"
        if "error" in text:
            return "ERROR"
        if "exitoso" in text or "success" in text:
            return "DONE"
        return "OTHER"
