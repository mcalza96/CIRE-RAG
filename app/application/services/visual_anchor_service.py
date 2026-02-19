from __future__ import annotations

import asyncio
import math
import struct
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

from app.application.services.ingestion_state_manager import IngestionStateManager
from app.core.caching.image_hasher import ImageHasher
from app.core.config.model_config import get_model_settings
from app.core.settings import settings
from app.core.models.schemas import VisualParseResult
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

    async def run_if_needed(
        self, doc_id: str, tenant_id: Optional[str], result: Any
    ) -> Dict[str, Any]:
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
            raise RuntimeError(
                "Visual anchor stitching requires at least one persisted text chunk."
            )

        stitched = 0
        degraded_inline = 0
        parse_failed = 0
        parse_failed_copyright = 0
        parse_failed_copyright_refs: list[dict[str, Any]] = []
        skipped = 0
        cache_hit = 0
        cache_miss = 0
        parse_durations_ms: list[float] = []

        prepared_cache_inputs, prepare_stats = await self._prepare_cache_inputs(
            visual_tasks=visual_tasks
        )
        skipped += int(prepare_stats.get("skipped_small", 0))
        skipped += int(prepare_stats.get("skipped_duplicate", 0))

        prefetched_cache: dict[str, dict[str, Any]] = {}
        if bool(settings.VISUAL_CACHE_BATCH_PREFETCH_ENABLED) and prepared_cache_inputs:
            prefetched_cache = await self._prefetch_visual_cache(prepared_cache_inputs)

        candidate_entries: list[dict[str, Any]] = []
        for task in visual_tasks:
            page = int(task.get("page", 0) or 0)
            content_type = str(task.get("content_type", "table"))
            image_path = task.get("image_path")
            if not isinstance(image_path, str) or not image_path:
                skipped += 1
                continue

            task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            prepared_key = (image_path, content_type, self._source_metadata_key(task_metadata))
            prepared = prepared_cache_inputs.get(prepared_key)
            if prepared is None:
                skipped += 1
                continue

            parent_chunk = self._select_parent_chunk(
                page=page, page_to_chunk=page_to_chunk, fallback_chunk=fallback_chunk
            )
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
            check = (
                await client.table("content_chunks")
                .select("id")
                .eq("id", str(parent_chunk_id))
                .limit(1)
                .execute()
            )
            if not check.data:
                persisted_parent = await self._resolve_persisted_parent_chunk(
                    client=client, doc_id=doc_id, page=page
                )
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

            candidate_entries.append(
                {
                    "page": page,
                    "content_type": content_type,
                    "image_path": image_path,
                    "task_metadata": task_metadata,
                    "prepared": prepared,
                    "parent_chunk": parent_chunk,
                    "parent_chunk_id": str(parent_chunk_id),
                    "parent_chunk_content": str(parent_chunk_content),
                }
            )

        parsed_entries = await self._parse_visual_entries_concurrently(
            candidate_entries=candidate_entries,
            prefetched_cache=prefetched_cache,
        )

        parent_content_state: dict[str, str] = {
            str(entry.get("parent_chunk_id") or ""): str(entry.get("parent_chunk_content") or "")
            for entry in candidate_entries
            if str(entry.get("parent_chunk_id") or "")
        }

        for parsed in parsed_entries:
            entry = parsed.get("entry") if isinstance(parsed, dict) else None
            if not isinstance(entry, dict):
                skipped += 1
                continue

            parent_chunk = (
                entry.get("parent_chunk") if isinstance(entry.get("parent_chunk"), dict) else {}
            )
            parent_chunk_id = str(entry.get("parent_chunk_id") or "")
            page = int(entry.get("page") or 0)
            content_type = str(entry.get("content_type") or "table")
            image_path = str(entry.get("image_path") or "")
            task_metadata = (
                entry.get("task_metadata") if isinstance(entry.get("task_metadata"), dict) else {}
            )
            parse_result = parsed.get("parse_result")
            parse_error = parsed.get("parse_error")

            raw_cache_hit = parsed.get("cache_hit")
            if isinstance(raw_cache_hit, bool):
                if raw_cache_hit:
                    cache_hit += 1
                else:
                    cache_miss += 1

            raw_duration = parsed.get("parse_duration_ms")
            try:
                if raw_duration is not None:
                    parse_durations_ms.append(float(raw_duration))
            except (TypeError, ValueError):
                pass

            raw_mode = task_metadata.get("embedding_mode") or task_metadata.get("jina_mode")
            embedding_mode = str(raw_mode).upper() if raw_mode else None
            if embedding_mode not in {"LOCAL", "CLOUD"}:
                embedding_mode = None
            raw_provider = task_metadata.get("embedding_provider")
            embedding_provider = str(raw_provider).strip().lower() if raw_provider else None
            if embedding_provider == "jina" and embedding_mode is None:
                embedding_mode = str(settings.JINA_MODE or "CLOUD").upper()

            parent_chunk_content = parent_content_state.get(
                parent_chunk_id,
                str(entry.get("parent_chunk_content") or ""),
            )

            try:
                if parse_error is not None:
                    raise RuntimeError(str(parse_error))
                if parse_result is None:
                    raise RuntimeError("visual_parse_missing_result")

                integration = await self.visual_integrator.integrate_visual_node(
                    parent_chunk_id=parent_chunk_id,
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
                    embedding_provider=embedding_provider,
                )

                updated_content = parent_chunk_content + "\n\n" + integration.anchor_token
                parent_content_state[parent_chunk_id] = updated_content
                parent_chunk["content"] = updated_content
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
                        image_name = image_path.rsplit("/", 1)[-1] if image_path else ""
                        parse_failed_copyright_refs.append(
                            {
                                "page": page,
                                "parent_chunk_id": parent_chunk_id,
                                "image": image_name,
                            }
                        )

                logger.warning(
                    "visual_anchor_stitch_failed_inline_fallback",
                    doc_id=doc_id,
                    parent_chunk_id=parent_chunk_id,
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
                    chunk_id=parent_chunk_id,
                    content=fallback_content,
                )
                parent_content_state[parent_chunk_id] = fallback_content
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
                f" cache_hit={cache_hit}, cache_miss={cache_miss}, "
                f"parse_p50_ms={self._percentile(parse_durations_ms, 50)}, "
                f"parse_p95_ms={self._percentile(parse_durations_ms, 95)}."
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
            "cache_hit": cache_hit,
            "cache_miss": cache_miss,
            "cache_hit_rate": self._safe_ratio(cache_hit, cache_hit + cache_miss),
            "parse_p50_ms": self._percentile(parse_durations_ms, 50),
            "parse_p95_ms": self._percentile(parse_durations_ms, 95),
        }

    async def _resolve_persisted_parent_chunk(
        self, client: Any, doc_id: str, page: int
    ) -> Optional[Dict[str, Any]]:
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
        await (
            client.table("content_chunks").update({"content": content}).eq("id", chunk_id).execute()
        )

    @staticmethod
    def _build_inline_visual_fallback(
        parent_chunk_content: str, content_type: str, parse_result: Any
    ) -> str:
        markdown = getattr(parse_result, "markdown_content", "")
        safe_markdown = markdown.strip() if isinstance(markdown, str) else ""
        if not safe_markdown:
            safe_markdown = "[VISUAL_PARSE_UNAVAILABLE]"

        block = f'<visual_fallback type="{content_type}">\n{safe_markdown}\n</visual_fallback>'
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

    @staticmethod
    def _percentile(values: list[float], percentile: int) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return round(ordered[0], 2)
        idx = math.ceil((percentile / 100) * len(ordered)) - 1
        idx = max(0, min(idx, len(ordered) - 1))
        return round(ordered[idx], 2)

    @staticmethod
    def _safe_ratio(numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round(numerator / denominator, 4)

    @staticmethod
    def _source_metadata_key(source_metadata: dict[str, Any]) -> str:
        page = source_metadata.get("page")
        if page is None:
            return ""
        return str(page)

    @staticmethod
    def _cache_key(*, image_hash: str, content_type: str) -> str:
        return f"{image_hash}:{content_type.strip().lower()}"

    async def _prepare_cache_inputs(
        self, visual_tasks: list[dict[str, Any]]
    ) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, int]]:
        prepared: dict[tuple[str, str, str], dict[str, Any]] = {}
        stats = {"skipped_small": 0, "skipped_duplicate": 0}
        dedupe_enabled = bool(getattr(settings, "VISUAL_DEDUP_IN_DOCUMENT", True))
        seen_hash_content: set[tuple[str, str]] = set()
        for task in visual_tasks:
            image_path = task.get("image_path")
            if not isinstance(image_path, str) or not image_path:
                continue

            content_type = str(task.get("content_type", "table") or "table").strip().lower()
            raw_metadata = task.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            key = (image_path, content_type, self._source_metadata_key(metadata))
            if key in prepared:
                continue

            try:
                image_bytes = await asyncio.to_thread(Path(image_path).read_bytes)
                if self._is_small_image(image_bytes):
                    stats["skipped_small"] = int(stats.get("skipped_small", 0)) + 1
                    continue

                image_hash = ImageHasher.sha256_bytes(image_bytes)
                dedupe_key = (image_hash, content_type)
                if dedupe_enabled and dedupe_key in seen_hash_content:
                    stats["skipped_duplicate"] = int(stats.get("skipped_duplicate", 0)) + 1
                    continue
                seen_hash_content.add(dedupe_key)

                prepared[key] = {
                    "image_bytes": image_bytes,
                    "image_hash": image_hash,
                    "content_type": content_type,
                }
            except Exception as exc:
                logger.warning(
                    "visual_cache_input_prepare_failed", image_path=image_path, error=str(exc)
                )
        return prepared, stats

    async def _parse_visual_entries_concurrently(
        self,
        *,
        candidate_entries: list[dict[str, Any]],
        prefetched_cache: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not candidate_entries:
            return []

        max_parallel = max(1, int(getattr(settings, "VISUAL_PIPELINE_MAX_PARALLEL", 3) or 3))
        semaphore = asyncio.Semaphore(max_parallel)

        async def parse_one(entry: dict[str, Any]) -> dict[str, Any]:
            prepared = entry.get("prepared") if isinstance(entry.get("prepared"), dict) else {}
            content_type = str(entry.get("content_type") or "table")
            image_path = str(entry.get("image_path") or "")
            task_metadata = (
                entry.get("task_metadata") if isinstance(entry.get("task_metadata"), dict) else {}
            )
            image_hash = str(prepared.get("image_hash") or "")

            prefetched_row = (
                prefetched_cache.get(
                    self._cache_key(image_hash=image_hash, content_type=content_type)
                )
                if image_hash
                else None
            )
            if prefetched_row is not None:
                parse_result = self._parse_result_from_cache_row(
                    row=prefetched_row,
                    image_hash=image_hash,
                    content_type=content_type,
                )
                return {
                    "entry": entry,
                    "parse_result": parse_result,
                    "parse_error": None,
                    "cache_hit": True,
                    "parse_duration_ms": 0.0,
                }

            try:
                async with semaphore:
                    parse_result = await self.visual_parser.parse_image(
                        image_path=image_path,
                        image_bytes=(
                            prepared.get("image_bytes")
                            if isinstance(prepared.get("image_bytes"), bytes)
                            else None
                        ),
                        content_type=content_type,
                        source_metadata=task_metadata,
                    )
                parse_meta = (
                    parse_result.visual_metadata
                    if isinstance(parse_result.visual_metadata, dict)
                    else {}
                )
                return {
                    "entry": entry,
                    "parse_result": parse_result,
                    "parse_error": None,
                    "cache_hit": parse_meta.get("cache_hit"),
                    "parse_duration_ms": parse_meta.get("parse_duration_ms"),
                }
            except Exception as exc:
                return {
                    "entry": entry,
                    "parse_result": None,
                    "parse_error": str(exc),
                    "cache_hit": None,
                    "parse_duration_ms": None,
                }

        return await asyncio.gather(*(parse_one(entry) for entry in candidate_entries))

    @staticmethod
    def _is_small_image(image_bytes: bytes) -> bool:
        min_bytes = max(0, int(getattr(settings, "VISUAL_MIN_IMAGE_BYTES", 16384) or 16384))
        if len(image_bytes) < min_bytes:
            return True

        min_w = max(1, int(getattr(settings, "VISUAL_MIN_IMAGE_WIDTH", 200) or 200))
        min_h = max(1, int(getattr(settings, "VISUAL_MIN_IMAGE_HEIGHT", 200) or 200))
        dims = VisualAnchorService._read_image_dimensions(image_bytes)
        if dims is None:
            return False
        width, height = dims
        return width < min_w or height < min_h

    @staticmethod
    def _read_image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
        if not image_bytes:
            return None

        try:
            from PIL import Image  # type: ignore

            with Image.open(BytesIO(image_bytes)) as img:
                return int(img.width), int(img.height)
        except Exception:
            pass

        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
            try:
                width = struct.unpack(">I", image_bytes[16:20])[0]
                height = struct.unpack(">I", image_bytes[20:24])[0]
                return int(width), int(height)
            except Exception:
                return None

        if image_bytes.startswith(b"\xff\xd8"):
            idx = 2
            total = len(image_bytes)
            sof_markers = {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            while idx + 9 < total:
                if image_bytes[idx] != 0xFF:
                    idx += 1
                    continue
                marker = image_bytes[idx + 1]
                idx += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if idx + 2 > total:
                    break
                segment_len = int.from_bytes(image_bytes[idx : idx + 2], "big")
                if segment_len < 2 or idx + segment_len > total:
                    break
                if marker in sof_markers:
                    if idx + 7 <= total:
                        height = int.from_bytes(image_bytes[idx + 3 : idx + 5], "big")
                        width = int.from_bytes(image_bytes[idx + 5 : idx + 7], "big")
                        return int(width), int(height)
                    break
                idx += segment_len

        return None

    async def _prefetch_visual_cache(
        self,
        prepared_inputs: dict[tuple[str, str, str], dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not prepared_inputs:
            return {}

        model_settings = get_model_settings()
        provider = model_settings.resolved_ingest_provider.value
        model_version = model_settings.resolved_ingest_model_name
        prompt_version = str(settings.VISUAL_CACHE_PROMPT_VERSION or "v1").strip()
        schema_version = str(settings.VISUAL_CACHE_SCHEMA_VERSION or "VisualParseResult:v1").strip()

        hashes = sorted(
            {str(item["image_hash"]) for item in prepared_inputs.values() if item.get("image_hash")}
        )
        content_types = sorted(
            {
                str(item["content_type"])
                for item in prepared_inputs.values()
                if item.get("content_type")
            }
        )
        if not hashes:
            return {}

        client = await get_async_supabase_client()
        try:
            query = (
                client.table("cache_visual_extractions")
                .select(
                    "image_hash,content_type,prompt_version,schema_version,result_data,created_at"
                )
                .eq("provider", provider)
                .eq("model_version", model_version)
                .in_("image_hash", hashes)
            )

            if bool(settings.VISUAL_CACHE_KEY_V2_ENABLED):
                query = (
                    query.eq("prompt_version", prompt_version)
                    .eq("schema_version", schema_version)
                    .in_("content_type", content_types or ["table"])
                )

            response = await query.execute()
        except Exception as exc:
            logger.warning("visual_cache_prefetch_failed", error=str(exc))
            return {}

        rows = response.data or []
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            image_hash = str(row.get("image_hash") or "")
            content_type = str(row.get("content_type") or "table").strip().lower()
            if not image_hash:
                continue
            result[self._cache_key(image_hash=image_hash, content_type=content_type)] = row
        return result

    def _parse_result_from_cache_row(
        self, *, row: dict[str, Any], image_hash: str, content_type: str
    ) -> VisualParseResult:
        result_data = row.get("result_data") or {}
        parsed = VisualParseResult.model_validate(result_data)
        metadata = parsed.visual_metadata if isinstance(parsed.visual_metadata, dict) else {}
        model_settings = get_model_settings()
        metadata["cache_hit"] = True
        metadata["cache_key"] = {
            "image_hash": image_hash,
            "provider": model_settings.resolved_ingest_provider.value,
            "model_version": model_settings.resolved_ingest_model_name,
            "content_type": content_type,
            "prompt_version": str(settings.VISUAL_CACHE_PROMPT_VERSION or "v1").strip(),
            "schema_version": str(
                settings.VISUAL_CACHE_SCHEMA_VERSION or "VisualParseResult:v1"
            ).strip(),
        }
        metadata["parse_duration_ms"] = 0.0
        created_at = row.get("created_at")
        if created_at:
            metadata["cache_created_at"] = str(created_at)
            metadata["cache_age_seconds"] = self._safe_cache_age_seconds(str(created_at))
        parsed.visual_metadata = metadata
        return parsed

    @staticmethod
    def _safe_cache_age_seconds(created_at: str) -> float | None:
        if not created_at:
            return None
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - created.astimezone(timezone.utc)
            return round(max(age.total_seconds(), 0.0), 2)
        except Exception:
            return None
