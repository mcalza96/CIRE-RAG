"""Atomic visual-node integration service for Visual Anchor RAG."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.ai.schemas import VisualParseResult
from app.infrastructure.settings import settings
from app.utils.text_processing import inject_anchor_token
from app.infrastructure.supabase.client import get_async_supabase_client
from app.ai.embeddings.embedding_service import JinaEmbeddingService

logger = structlog.get_logger(__name__)


class VisualIntegrationError(RuntimeError):
    """Raised when visual-node stitching cannot be completed atomically."""


@dataclass(frozen=True)
class VisualIntegrationResult:
    """Result object returned after successful visual-node integration."""

    visual_node_id: str
    parent_chunk_id: str
    parent_table: str
    storage_path: str
    public_url: str | None
    anchor_token: str


class VisualGraphIntegrator:
    """Orchestrates upload, token injection and transactional DB stitching."""

    def __init__(
        self,
        bucket_name: str = "visual_assets",
        rpc_name: str = "create_visual_node_transaction",
        embedding_service: JinaEmbeddingService | None = None,
        supabase_client: Any | None = None,
    ) -> None:
        """Initialize integrator with dependency-injected collaborators."""

        self._bucket_name = bucket_name
        self._rpc_name = rpc_name
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._supabase_client = supabase_client

    async def integrate_visual_node(
        self,
        parent_chunk_id: str | UUID,
        parent_chunk_text: str,
        image_path: str | Path,
        parse_result: VisualParseResult,
        content_type: str,
        anchor_context: dict[str, Any] | None = None,
        parent_chunk_table: str | None = None,
        metadata: dict[str, Any] | None = None,
        embedding_mode: str | None = None,
        embedding_provider: str | None = None,
    ) -> VisualIntegrationResult:
        """Persist visual node and parent anchor in a consistency-safe sequence."""

        client = await self._get_client()
        parent_chunk_id_str = str(parent_chunk_id)
        node_id = uuid4()

        short_summary = self._short_summary(parse_result.dense_summary)
        context = dict(anchor_context or {})
        context.setdefault("type", content_type)
        context.setdefault("short_summary", short_summary)

        updated_parent_text = inject_anchor_token(
            text=parent_chunk_text,
            context=context,
            node_id=node_id,
        )
        anchor_token = self._extract_anchor_token(
            updated_parent_text=updated_parent_text, node_id=node_id
        )

        embedding = await self._generate_summary_embedding(
            parse_result.dense_summary,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider,
        )
        storage_path = self._build_storage_path(
            parent_chunk_id=parent_chunk_id_str, node_id=node_id, image_path=image_path
        )

        uploaded = False
        public_url: str | None = None
        try:
            public_url = await self._upload_visual_asset(
                client=client, image_path=image_path, storage_path=storage_path
            )
            uploaded = True

            structured_reconstruction = {
                "markdown": parse_result.markdown_content,
                "metadata": {
                    **(metadata or {}),
                    **parse_result.visual_metadata,
                    "content_type": content_type,
                    "storage_path": storage_path,
                    "public_url": public_url,
                    "embedding_provider": embedding_provider,
                    "embedding_mode": embedding_mode,
                },
            }

            rpc_payload = {
                "p_visual_node_id": str(node_id),
                "p_parent_chunk_id": parent_chunk_id_str,
                "p_parent_chunk_text_with_anchor": updated_parent_text,
                "p_image_storage_path": storage_path,
                "p_visual_summary": parse_result.dense_summary,
                "p_structured_reconstruction": structured_reconstruction,
                "p_summary_embedding": embedding,
                "p_parent_chunk_table": parent_chunk_table or "content_chunks",
            }

            try:
                response = await client.rpc(self._rpc_name, rpc_payload).execute()
                rows = response.data or []
                if not rows:
                    raise VisualIntegrationError(
                        "RPC returned no rows; transaction result is unknown."
                    )

                parent_table = rows[0].get("parent_table") or (parent_chunk_table or "unknown")
                return VisualIntegrationResult(
                    visual_node_id=str(node_id),
                    parent_chunk_id=parent_chunk_id_str,
                    parent_table=parent_table,
                    storage_path=storage_path,
                    public_url=public_url,
                    anchor_token=anchor_token,
                )
            except Exception as rpc_exc:
                logger.warning(
                    "visual_integration_rpc_failed_using_fallback",
                    parent_chunk_id=parent_chunk_id_str,
                    error=str(rpc_exc),
                )

                fallback_parent_table = await self._fallback_stitch_without_rpc(
                    client=client,
                    node_id=str(node_id),
                    parent_chunk_id=parent_chunk_id_str,
                    parent_chunk_text=parent_chunk_text,
                    parent_chunk_text_with_anchor=updated_parent_text,
                    image_storage_path=storage_path,
                    visual_summary=parse_result.dense_summary,
                    structured_reconstruction=structured_reconstruction,
                    summary_embedding=embedding,
                    preferred_parent_table=parent_chunk_table,
                )
                return VisualIntegrationResult(
                    visual_node_id=str(node_id),
                    parent_chunk_id=parent_chunk_id_str,
                    parent_table=fallback_parent_table,
                    storage_path=storage_path,
                    public_url=public_url,
                    anchor_token=anchor_token,
                )
        except Exception as exc:
            logger.error(
                "visual_integration_failed",
                parent_chunk_id=parent_chunk_id_str,
                storage_path=storage_path,
                error=str(exc),
            )
            if uploaded:
                await self._safe_remove_uploaded_asset(client=client, storage_path=storage_path)
            raise VisualIntegrationError(f"Visual integration failed: {exc}") from exc

    async def _fallback_stitch_without_rpc(
        self,
        client: Any,
        node_id: str,
        parent_chunk_id: str,
        parent_chunk_text: str,
        parent_chunk_text_with_anchor: str,
        image_storage_path: str,
        visual_summary: str,
        structured_reconstruction: dict[str, Any],
        summary_embedding: list[float],
        preferred_parent_table: str | None,
    ) -> str:
        """Fallback stitching path when RPC fails in runtime environments."""

        parent_tables = self._candidate_parent_tables(preferred_parent_table)
        parent_table = await self._find_parent_table(
            client=client, parent_chunk_id=parent_chunk_id, candidates=parent_tables
        )
        if parent_table is None:
            raise VisualIntegrationError(
                f"Parent chunk not found in candidate tables: {parent_tables}"
            )

        await self._update_parent_content(
            client=client,
            table=parent_table,
            chunk_id=parent_chunk_id,
            content=parent_chunk_text_with_anchor,
        )

        insert_exc: Exception | None = None
        try:
            await (
                client.table("visual_nodes")
                .insert(
                    {
                        "id": node_id,
                        "parent_chunk_id": parent_chunk_id,
                        "image_storage_path": image_storage_path,
                        "visual_summary": visual_summary,
                        "structured_reconstruction": structured_reconstruction,
                        "summary_embedding": summary_embedding,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .execute()
            )
        except Exception as direct_insert_exc:
            logger.warning(
                "visual_node_insert_embedding_list_failed_retrying_literal",
                parent_chunk_id=parent_chunk_id,
                error=str(direct_insert_exc),
            )
            try:
                await (
                    client.table("visual_nodes")
                    .insert(
                        {
                            "id": node_id,
                            "parent_chunk_id": parent_chunk_id,
                            "image_storage_path": image_storage_path,
                            "visual_summary": visual_summary,
                            "structured_reconstruction": structured_reconstruction,
                            "summary_embedding": self._vector_literal(summary_embedding),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute()
                )
            except Exception as literal_insert_exc:
                insert_exc = literal_insert_exc

        if insert_exc is not None:
            await self._update_parent_content(
                client=client,
                table=parent_table,
                chunk_id=parent_chunk_id,
                content=parent_chunk_text,
            )
            raise VisualIntegrationError(
                f"Fallback insert into visual_nodes failed: {insert_exc}"
            ) from insert_exc

        return parent_table

    @staticmethod
    def _candidate_parent_tables(preferred_parent_table: str | None) -> list[str]:
        """Return ordered candidate parent tables for fallback stitching."""

        ordered = [
            preferred_parent_table,
            "content_chunks",
            "document_chunks",
            "knowledge_chunks",
            "site_pages_sections",
        ]
        result: list[str] = []
        for table in ordered:
            if table and table not in result:
                result.append(table)
        return result

    async def _find_parent_table(
        self, client: Any, parent_chunk_id: str, candidates: list[str]
    ) -> str | None:
        """Find which candidate table contains the parent chunk id."""

        for table in candidates:
            try:
                response = (
                    await client.table(table)
                    .select("id")
                    .eq("id", parent_chunk_id)
                    .limit(1)
                    .execute()
                )
                rows = response.data or []
                if rows:
                    return table
            except Exception:
                continue
        return None

    async def _update_parent_content(
        self, client: Any, table: str, chunk_id: str, content: str
    ) -> None:
        """Update parent chunk content with optional updated_at compatibility."""

        try:
            await (
                client.table(table)
                .update({"content": content, "updated_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", chunk_id)
                .execute()
            )
            return
        except Exception:
            await client.table(table).update({"content": content}).eq("id", chunk_id).execute()

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        """Serialize float list into pgvector literal format."""

        return "[" + ",".join(str(v) for v in values) + "]"

    async def _get_client(self) -> Any:
        """Return Supabase client from DI override or shared singleton."""

        if self._supabase_client is not None:
            return self._supabase_client
        self._supabase_client = await get_async_supabase_client()
        return self._supabase_client

    async def _upload_visual_asset(
        self, client: Any, image_path: str | Path, storage_path: str
    ) -> str | None:
        """Upload image to Supabase Storage and return public URL when available."""

        path = Path(image_path)
        if not path.exists():
            raise VisualIntegrationError(f"Image path does not exist: {path}")

        payload = await asyncio.to_thread(path.read_bytes)
        content_type = self._guess_content_type(path)

        storage = client.storage.from_(self._bucket_name)
        try:
            await storage.upload(
                path=storage_path,
                file=payload,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        except TypeError:
            await storage.upload(
                storage_path, payload, {"content-type": content_type, "upsert": "false"}
            )

        return await self._extract_public_url(storage=storage, storage_path=storage_path)

    async def _safe_remove_uploaded_asset(self, client: Any, storage_path: str) -> None:
        """Best-effort cleanup for orphaned uploads after RPC failure."""

        try:
            await client.storage.from_(self._bucket_name).remove([storage_path])
            logger.info("orphan_visual_asset_removed", storage_path=storage_path)
        except Exception as cleanup_exc:
            logger.warning(
                "orphan_visual_asset_cleanup_failed",
                storage_path=storage_path,
                error=str(cleanup_exc),
            )

    async def _generate_summary_embedding(
        self,
        summary: str,
        embedding_mode: str | None = None,
        embedding_provider: str | None = None,
    ) -> list[float]:
        """Generate embedding for dense retrieval indexing."""

        mode = str(embedding_mode or settings.JINA_MODE or "LOCAL").upper()
        embeddings = await self._embedding_service.embed_texts(
            [summary],
            task="retrieval.passage",
            mode=mode,
            provider=embedding_provider,
        )
        if not embeddings or not embeddings[0]:
            raise VisualIntegrationError("Embedding generation returned empty vector.")
        return embeddings[0]

    @staticmethod
    def _build_storage_path(parent_chunk_id: str, node_id: UUID, image_path: str | Path) -> str:
        """Build deterministic storage path for visual assets."""

        suffix = Path(image_path).suffix.lower() or ".png"
        return f"visual_nodes/{parent_chunk_id}/{node_id}{suffix}"

    @staticmethod
    def _guess_content_type(path: Path) -> str:
        """Resolve MIME type for upload metadata."""

        suffix = path.suffix.lower()
        if suffix == ".jpg" or suffix == ".jpeg":
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        return "image/png"

    @staticmethod
    async def _extract_public_url(storage: Any, storage_path: str) -> str | None:
        """Best-effort extraction of public URL across SDK variants."""

        try:
            value = storage.get_public_url(storage_path)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return None

        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            if "publicUrl" in value:
                return value["publicUrl"]
            data = value.get("data")
            if isinstance(data, dict):
                return data.get("publicUrl")
        return None

    @staticmethod
    def _extract_anchor_token(updated_parent_text: str, node_id: UUID) -> str:
        """Return exact anchor token inserted for downstream observability."""

        marker = f"<<VISUAL_ANCHOR: {node_id}"
        start = updated_parent_text.find(marker)
        if start < 0:
            return marker
        end = updated_parent_text.find(">>", start)
        if end < 0:
            return updated_parent_text[start:]
        return updated_parent_text[start : end + 2]

    @staticmethod
    def _short_summary(summary: str, max_chars: int = 96) -> str:
        """Build compact description for token payload."""

        compact = " ".join(summary.split()).strip()
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 3].rstrip() + "..."
