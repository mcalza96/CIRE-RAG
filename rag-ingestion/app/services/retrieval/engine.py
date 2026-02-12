"""Unified retrieval engine with visual-node hydration logic."""

from __future__ import annotations

from typing import Any

import structlog

from app.core.models.context import ContextItem, UnifiedSearchRow
from app.core.retrieval_config import retrieval_settings
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService

logger = structlog.get_logger(__name__)


class UnifiedRetrievalEngine:
    """Executes unified search and hydrates visual nodes for LLM-ready context."""

    def __init__(
        self,
        embedding_service: JinaEmbeddingService | None = None,
        supabase_client: Any | None = None,
        rpc_name: str = "unified_search_context_v2",
    ) -> None:
        """Initialize engine with dependency-injected collaborators."""

        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._supabase_client = supabase_client
        self._rpc_name = rpc_name

    async def retrieve_context(
        self,
        query: str,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 40,
        similarity_threshold: float | None = None,
        hydration_threshold: float = 0.35,
    ) -> list[ContextItem]:
        """Run vector search over text+visual sources and return hydrated context items."""

        if not query.strip():
            return []

        query_vector = await self._embed_query(query)
        client = await self._get_client()

        rpc_params = {
            "p_query_embedding": query_vector,
            "p_query_text": query,
            "p_match_count": max(fetch_k, k),
            "p_match_threshold": similarity_threshold or retrieval_settings.MATCH_THRESHOLD_DEFAULT,
            "p_hydration_threshold": hydration_threshold,
            "p_filter_conditions": scope_context or {},
        }

        response = await client.rpc(self._rpc_name, rpc_params).execute()
        rows = response.data or []

        hydrated: list[ContextItem] = []
        for row in rows:
            parsed = UnifiedSearchRow.model_validate(row)
            if parsed.source_type == "visual_node" and parsed.structured_reconstruction is None:
                parsed.structured_reconstruction = await self._load_visual_payload(parsed.id)
            item = self._hydrate_row(parsed)
            hydrated.append(item)

        return hydrated[:k]

    async def _embed_query(self, query: str) -> list[float]:
        """Generate query embedding for retrieval."""

        vectors = await self._embedding_service.embed_texts([query], task="retrieval.query")
        if not vectors or not vectors[0]:
            raise ValueError("Failed to generate query embedding.")
        return vectors[0]

    async def _get_client(self) -> Any:
        """Return Supabase client from DI override or global singleton."""

        if self._supabase_client is not None:
            return self._supabase_client
        self._supabase_client = await get_async_supabase_client()
        return self._supabase_client

    async def _load_visual_payload(self, visual_node_id: str) -> dict[str, Any] | None:
        """Lazy-load structured reconstruction when omitted by hydration threshold."""

        client = await self._get_client()
        response = (
            await client.table("visual_nodes")
            .select("structured_reconstruction")
            .eq("id", visual_node_id)
            .maybe_single()
            .execute()
        )
        row = response.data or {}
        payload = row.get("structured_reconstruction")
        return payload if isinstance(payload, dict) else None

    def _hydrate_row(self, row: UnifiedSearchRow) -> ContextItem:
        """Apply bait-and-switch hydration policy by source type."""

        if row.source_type == "visual_node":
            return self._hydrate_visual_row(row)

        return ContextItem(
            id=row.id,
            source_type=row.source_type,
            content=row.content or "",
            similarity=row.similarity,
            score=row.score,
            metadata=row.metadata,
            is_visual_anchor=False,
            source_id=row.source_id,
            parent_chunk_id=row.parent_chunk_id,
        )

    def _hydrate_visual_row(self, row: UnifiedSearchRow) -> ContextItem:
        """Hydrate visual result using structured_reconstruction, not visual_summary."""

        payload = row.structured_reconstruction or {}
        markdown = self._extract_markdown(payload)
        visual_type = self._extract_visual_type(payload, row.metadata)

        hydrated_text = (
            f"<visual_context id=\"{row.id}\" type=\"{visual_type}\">\n"
            f"{markdown}\n"
            "</visual_context>"
        )

        metadata = dict(row.metadata)
        metadata["hydrated_from"] = "structured_reconstruction"
        metadata["visual_summary"] = row.visual_summary

        return ContextItem(
            id=row.id,
            source_type=row.source_type,
            content=hydrated_text,
            similarity=row.similarity,
            score=row.score,
            metadata=metadata,
            is_visual_anchor=True,
            source_id=row.source_id,
            parent_chunk_id=row.parent_chunk_id,
        )

    @staticmethod
    def _extract_markdown(payload: dict[str, Any]) -> str:
        """Extract markdown content from structured reconstruction payload."""

        markdown = payload.get("markdown")
        if isinstance(markdown, str) and markdown.strip():
            return markdown

        if payload:
            return str(payload)
        return "[STRUCTURED_RECONSTRUCTION_NOT_AVAILABLE]"

    @staticmethod
    def _extract_visual_type(payload: dict[str, Any], metadata: dict[str, Any]) -> str:
        """Resolve visual context type from payload metadata."""

        payload_meta = payload.get("metadata")
        if isinstance(payload_meta, dict):
            value = payload_meta.get("type") or payload_meta.get("content_type")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()

        value = metadata.get("type") or metadata.get("content_type")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

        return "table"
