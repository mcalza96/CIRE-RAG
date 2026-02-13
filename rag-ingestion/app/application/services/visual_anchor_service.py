from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

from app.application.services.ingestion_state_manager import IngestionStateManager
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.ingestion.integrator import VisualGraphIntegrator
from app.services.ingestion.visual_parser import VisualDocumentParser

logger = structlog.get_logger(__name__)


class VisualAnchorService:
    def __init__(
        self,
        state_manager: IngestionStateManager,
        visual_parser: VisualDocumentParser,
        visual_integrator: VisualGraphIntegrator,
    ) -> None:
        self.state_manager = state_manager
        self.visual_parser = visual_parser
        self.visual_integrator = visual_integrator

    async def run_if_needed(self, doc_id: str, tenant_id: Optional[str], result: Any) -> Dict[str, Any]:
        routing = (result.metadata or {}).get("routing", {}) if hasattr(result, "metadata") else {}
        visual_tasks = routing.get("visual_tasks", []) if isinstance(routing, dict) else []
        if not visual_tasks:
            return {
                "attempted": 0,
                "stitched": 0,
                "degraded_inline": 0,
                "parse_failed": 0,
                "skipped": 0,
            }

        page_to_chunk: dict[int, dict[str, Any]] = {}
        fallback_chunk: dict[str, Any] | None = None
        for chunk in result.chunks:
            if not isinstance(chunk, dict):
                continue
            page = chunk.get("file_page_number")
            if isinstance(page, int) and page not in page_to_chunk:
                page_to_chunk[page] = chunk
            if fallback_chunk is None:
                fallback_chunk = chunk

        if fallback_chunk is None:
            raise RuntimeError("Visual anchor stitching requires at least one persisted text chunk.")

        stitched = 0
        degraded_inline = 0
        parse_failed = 0
        parse_failed_copyright = 0
        parse_failed_copyright_refs: list[dict[str, Any]] = []
        skipped = 0
        for task in visual_tasks:
            page = int(task.get("page", 0) or 0)
            content_type = str(task.get("content_type", "table"))
            image_path = task.get("image_path")
            if not isinstance(image_path, str) or not image_path:
                skipped += 1
                continue

            parent_chunk = self._select_parent_chunk(page=page, page_to_chunk=page_to_chunk, fallback_chunk=fallback_chunk)
            parent_chunk_id = parent_chunk.get("id")
            parent_chunk_content = parent_chunk.get("content", "")
            if not parent_chunk_id or not isinstance(parent_chunk_content, str):
                logger.warning(
                    "visual_anchor_invalid_parent_chunk",
                    doc_id=doc_id,
                    page=page,
                )
                skipped += 1
                continue

            client = await get_async_supabase_client()
            check = await client.table("content_chunks").select("id").eq("id", str(parent_chunk_id)).limit(1).execute()
            if not check.data:
                persisted_parent = await self._resolve_persisted_parent_chunk(client=client, doc_id=doc_id, page=page)
                if persisted_parent is None:
                    logger.warning(
                        "visual_anchor_parent_not_in_db",
                        parent_chunk_id=str(parent_chunk_id),
                        page=page,
                        doc_id=doc_id,
                    )
                    skipped += 1
                    continue

                logger.info(
                    "visual_anchor_parent_rebound",
                    doc_id=doc_id,
                    page=page,
                    old_parent_chunk_id=str(parent_chunk_id),
                    rebound_parent_chunk_id=str(persisted_parent.get("id")),
                )
                parent_chunk_id = str(persisted_parent.get("id"))
                parent_chunk_content = str(persisted_parent.get("content") or "")

            parse_result: Any | None = None

            try:
                task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                raw_mode = task_metadata.get("embedding_mode") or task_metadata.get("jina_mode")
                embedding_mode = str(raw_mode).upper() if raw_mode else None
                if embedding_mode not in {"LOCAL", "CLOUD"}:
                    embedding_mode = None

                parse_result = await self.visual_parser.parse_image(
                    image_path=image_path,
                    content_type=content_type,
                    source_metadata=task_metadata,
                )

                integration = await self.visual_integrator.integrate_visual_node(
                    parent_chunk_id=str(parent_chunk_id),
                    parent_chunk_text=parent_chunk_content,
                    image_path=image_path,
                    parse_result=parse_result,
                    content_type=content_type,
                    anchor_context={
                        "type": content_type,
                        "short_summary": parse_result.dense_summary,
                    },
                    metadata=task_metadata,
                    embedding_mode=embedding_mode,
                )

                parent_chunk["content"] = parent_chunk_content + "\n\n" + integration.anchor_token
                stitched += 1
            except Exception as integration_exc:
                if parse_result is None:
                    parse_failed += 1
                    err_text = str(integration_exc).lower()
                    if (
                        "gemini_copyright_block" in err_text
                        or ("finish_reason" in err_text and "4" in err_text)
                        or "copyright" in err_text
                    ):
                        parse_failed_copyright += 1
                        image_name = image_path.rsplit("/", 1)[-1] if isinstance(image_path, str) else ""
                        parse_failed_copyright_refs.append(
                            {
                                "page": page,
                                "parent_chunk_id": str(parent_chunk_id),
                                "image": image_name,
                            }
                        )

                logger.warning(
                    "visual_anchor_stitch_failed_inline_fallback",
                    doc_id=doc_id,
                    parent_chunk_id=str(parent_chunk_id),
                    page=page,
                    stage="parse" if parse_result is None else "integrate",
                    error=str(integration_exc),
                )

                fallback_content = self._build_inline_visual_fallback(
                    parent_chunk_content=parent_chunk_content,
                    content_type=content_type,
                    parse_result=parse_result,
                )
                await self._persist_chunk_content_fallback(
                    chunk_id=str(parent_chunk_id),
                    content=fallback_content,
                )
                parent_chunk["content"] = fallback_content
                degraded_inline += 1

        await self.state_manager.log_step(
            doc_id,
            (
                "Visual anchor stitching completado: "
                f"{stitched}/{len(visual_tasks)} nodos visuales, "
                f"fallback_inline={degraded_inline}, "
                f"parse_failed={parse_failed}, "
                f"parse_failed_copyright={parse_failed_copyright}, skipped={skipped}."
            ),
            "SUCCESS",
            tenant_id=tenant_id,
        )
        return {
            "attempted": len(visual_tasks),
            "stitched": stitched,
            "degraded_inline": degraded_inline,
            "parse_failed": parse_failed,
            "parse_failed_copyright": parse_failed_copyright,
            "parse_failed_copyright_refs": parse_failed_copyright_refs,
            "skipped": skipped,
        }

    async def _resolve_persisted_parent_chunk(self, client: Any, doc_id: str, page: int) -> Optional[Dict[str, Any]]:
        if page > 0:
            by_page = (
                await client.table("content_chunks")
                .select("id,content,file_page_number")
                .eq("source_id", doc_id)
                .eq("file_page_number", page)
                .limit(1)
                .execute()
            )
            rows = by_page.data or []
            if rows:
                row = rows[0]
                return row if isinstance(row, dict) else None

        nearest = (
            await client.table("content_chunks")
            .select("id,content,file_page_number")
            .eq("source_id", doc_id)
            .limit(1)
            .execute()
        )
        rows = nearest.data or []
        if not rows:
            return None
        row = rows[0]
        return row if isinstance(row, dict) else None

    async def _persist_chunk_content_fallback(self, chunk_id: str, content: str) -> None:
        client = await get_async_supabase_client()
        await client.table("content_chunks").update({"content": content}).eq("id", chunk_id).execute()

    @staticmethod
    def _build_inline_visual_fallback(parent_chunk_content: str, content_type: str, parse_result: Any) -> str:
        markdown = getattr(parse_result, "markdown_content", "")
        safe_markdown = markdown.strip() if isinstance(markdown, str) else ""
        if not safe_markdown:
            safe_markdown = "[VISUAL_PARSE_UNAVAILABLE]"

        block = (
            f"<visual_fallback type=\"{content_type}\">\n"
            f"{safe_markdown}\n"
            "</visual_fallback>"
        )
        if parent_chunk_content.endswith("\n"):
            return parent_chunk_content + block
        if parent_chunk_content:
            return parent_chunk_content + "\n\n" + block
        return block

    @staticmethod
    def _select_parent_chunk(
        page: int,
        page_to_chunk: dict[int, dict[str, Any]],
        fallback_chunk: dict[str, Any],
    ) -> dict[str, Any]:
        if page in page_to_chunk:
            return page_to_chunk[page]

        lower_pages = [p for p in page_to_chunk.keys() if p < page]
        if lower_pages:
            return page_to_chunk[max(lower_pages)]

        higher_pages = [p for p in page_to_chunk.keys() if p > page]
        if higher_pages:
            return page_to_chunk[min(higher_pages)]

        return fallback_chunk
