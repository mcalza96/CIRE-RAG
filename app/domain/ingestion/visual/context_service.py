from __future__ import annotations

import asyncio
import math
import struct
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog
from app.ai.contracts import VisualParseResult
from app.infrastructure.settings import settings
from app.infrastructure.caching.image_hasher import ImageHasher
from app.ai.config import get_model_settings

from .ports import IVisualIntegrator, IVisualParser
from ..ports import ISourceRepository, IVisualCacheRepository, IContentRepository

logger = structlog.get_logger(__name__)

class VisualContextService:
    """
    Robust service for managing visual context and document stitching.
    Replaces the anemic 'VisualAnchorService'.
    """
    def __init__(
        self,
        source_repository: ISourceRepository,
        content_repository: IContentRepository,
        visual_cache_repository: IVisualCacheRepository,
        visual_parser: IVisualParser,
        visual_integrator: IVisualIntegrator,
    ) -> None:
        self.source_repository = source_repository
        self.content_repository = content_repository
        self.visual_cache_repository = visual_cache_repository
        self.visual_parser = visual_parser
        self.visual_integrator = visual_integrator

    async def process_visual_tasks(
        self, doc_id: str, tenant_id: Optional[str], result: Any
    ) -> Dict[str, Any]:
        """
        Processes visual extraction tasks and stitches them into the document structure.
        """
        routing = (result.metadata or {}).get("routing", {}) if hasattr(result, "metadata") else {}
        visual_tasks = routing.get("visual_tasks", []) if isinstance(routing, dict) else []
        
        if not visual_tasks:
            return self._empty_stats()

        # Map pages to chunks for efficient sibling/parent lookup
        page_to_chunk, fallback_chunk = self._map_pages_to_chunks(result.chunks)
        if fallback_chunk is None:
            raise RuntimeError("Visual context stitching requires at least one persisted text chunk.")

        stats = self._initialize_stats(len(visual_tasks))
        
        # 1. Prepare and prefetch cache
        prepared_inputs, prepare_stats = await self._prepare_cache_inputs(visual_tasks)
        stats["skipped"] += prepare_stats["skipped_small"] + prepare_stats["skipped_duplicate"]

        prefetched_cache = await self._prefetch_visual_cache(prepared_inputs)

        # 2. Identify candidates and validate parent chunks
        candidate_entries = await self._identify_candidates(
            doc_id=doc_id,
            visual_tasks=visual_tasks,
            prepared_inputs=prepared_inputs,
            page_to_chunk=page_to_chunk,
            fallback_chunk=fallback_chunk,
            stats=stats
        )

        # 3. Parse and integrate
        parsed_entries = await self._parse_visual_entries_concurrently(
            candidate_entries=candidate_entries,
            prefetched_cache=prefetched_cache,
        )

        # Keep track of updated content to allow sequential anchor tokens
        parent_content_state: Dict[str, str] = {
            str(e["parent_chunk_id"]): str(e["parent_chunk_content"]) 
            for e in candidate_entries
        }

        for parsed in parsed_entries:
            await self._integrate_entry(parsed, parent_content_state, stats, doc_id)

        await self._finalize_stats(doc_id, tenant_id, stats)
        return stats

    def _empty_stats(self) -> Dict[str, Any]:
        return {
            "attempted": 0,
            "stitched": 0,
            "degraded_inline": 0,
            "parse_failed": 0,
            "skipped": 0,
        }

    def _initialize_stats(self, total: int) -> Dict[str, Any]:
        return {
            "attempted": total,
            "stitched": 0,
            "degraded_inline": 0,
            "parse_failed": 0,
            "parse_failed_copyright": 0,
            "parse_failed_copyright_refs": [],
            "skipped": 0,
            "cache_hit": 0,
            "cache_miss": 0,
            "durations_ms": [],
        }

    def _map_pages_to_chunks(self, chunks: List[Any]) -> Tuple[Dict[int, Dict[str, Any]], Optional[Dict[str, Any]]]:
        page_to_chunk: Dict[int, Dict[str, Any]] = {}
        fallback_chunk: Optional[Dict[str, Any]] = None
        for chunk in chunks:
            if not isinstance(chunk, dict): continue
            page = chunk.get("file_page_number")
            if isinstance(page, int) and page not in page_to_chunk:
                page_to_chunk[page] = chunk
            if fallback_chunk is None:
                fallback_chunk = chunk
        return page_to_chunk, fallback_chunk

    async def _identify_candidates(
        self, doc_id: str, visual_tasks: List[Dict[str, Any]], 
        prepared_inputs: Dict[Tuple[str, str, str], Dict[str, Any]], 
        page_to_chunk: Dict[int, Dict[str, Any]], fallback_chunk: Dict[str, Any],
        stats: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        candidates = []
        for task in visual_tasks:
            page = int(task.get("page", 0) or 0)
            content_type = str(task.get("content_type", "table"))
            image_path = task.get("image_path")
            
            if not image_path:
                stats["skipped"] += 1
                continue

            metadata = task.get("metadata") or {}
            key = (image_path, content_type, self._source_metadata_key(metadata))
            prepared = prepared_inputs.get(key)
            if not prepared:
                stats["skipped"] += 1
                continue

            parent_chunk = self._select_parent_chunk(page, page_to_chunk, fallback_chunk)
            parent_id = str(parent_chunk.get("id") or "")
            
            if not parent_id:
                stats["skipped"] += 1
                continue

            # Verify existence or rebound
            persisted = await self.content_repository.get_chunk_by_id(parent_id)
            if not persisted:
                persisted = await self.content_repository.get_first_chunk_by_page(doc_id, page)
                if not persisted:
                    stats["skipped"] += 1
                    continue
                parent_id = str(persisted["id"])

            candidates.append({
                "page": page,
                "content_type": content_type,
                "image_path": image_path,
                "task_metadata": metadata,
                "prepared": prepared,
                "parent_chunk": parent_chunk,
                "parent_chunk_id": parent_id,
                "parent_chunk_content": str(persisted.get("content") or ""),
            })
        return candidates

    async def _integrate_entry(
        self, parsed: Dict[str, Any], 
        content_state: Dict[str, str], 
        stats: Dict[str, Any], doc_id: str
    ) -> None:
        entry = parsed.get("entry")
        if not entry: return

        parent_id = entry["parent_chunk_id"]
        parse_result = parsed.get("parse_result")
        parse_error = parsed.get("parse_error")

        # Update cache stats
        if parsed.get("cache_hit"): stats["cache_hit"] += 1
        else: stats["cache_miss"] += 1
        
        if parsed.get("parse_duration_ms"):
            stats["durations_ms"].append(parsed["parse_duration_ms"])

        try:
            if parse_error: raise RuntimeError(parse_error)
            if not parse_result: raise RuntimeError("visual_parse_missing_result")

            integration = await self.visual_integrator.integrate_visual_node(
                parent_chunk_id=parent_id,
                parent_chunk_text=content_state[parent_id],
                image_path=entry["image_path"],
                parse_result=parse_result,
                content_type=entry["content_type"],
                anchor_context={
                    "type": entry["content_type"],
                    "short_summary": parse_result.dense_summary,
                },
                metadata=entry["task_metadata"]
            )

            updated = content_state[parent_id] + "\n\n" + integration.anchor_token
            content_state[parent_id] = updated
            entry["parent_chunk"]["content"] = updated
            stats["stitched"] += 1
            
        except Exception as exc:
            self._handle_integration_failure(exc, entry, content_state, stats, doc_id)

    def _handle_integration_failure(self, exc: Exception, entry: Dict[str, Any], content_state: Dict[str, str], stats: Dict[str, Any], doc_id: str) -> None:
        parent_id = entry["parent_chunk_id"]
        err_msg = str(exc).lower()
        
        if "copyright" in err_msg or "finish_reason" in err_msg:
            stats["parse_failed_copyright"] += 1
            stats["parse_failed_copyright_refs"].append({
                "page": entry["page"], "image": Path(entry["image_path"]).name
            })
        
        # Fallback to inline markdown
        fallback = self._build_inline_fallback(content_state[parent_id], entry["content_type"], entry.get("parse_result"))
        asyncio.create_task(self.content_repository.update_chunk_content(parent_id, fallback))
        content_state[parent_id] = fallback
        entry["parent_chunk"]["content"] = fallback
        stats["degraded_inline"] += 1

    async def _finalize_stats(self, doc_id: str, tenant_id: Optional[str], stats: Dict[str, Any]) -> None:
        msg = (
            f"Visual Context: {stats['stitched']} stitched, {stats['degraded_inline']} fallback, "
            f"{stats['cache_hit']} cache hits. parse_p95={self._percentile(stats['durations_ms'], 95)}ms"
        )
        await self.source_repository.log_event(doc_id, msg, "SUCCESS", tenant_id=tenant_id)

    # --- Domain Logic Helpers (Extracted from old anchor_service) ---

    def _build_inline_fallback(self, base_content: str, ctype: str, result: Optional[VisualParseResult]) -> str:
        md = result.markdown_content if result else "[VISUAL_PARSE_UNAVAILABLE]"
        block = f'<visual_fallback type="{ctype}">\n{md}\n</visual_fallback>'
        return f"{base_content}\n\n{block}" if base_content else block

    def _select_parent_chunk(self, page: int, page_to_chunk: Dict[int, Dict[str, Any]], fallback: Dict[str, Any]) -> Dict[str, Any]:
        if page in page_to_chunk: return page_to_chunk[page]
        low = [p for p in page_to_chunk if p < page]
        if low: return page_to_chunk[max(low)]
        high = [p for p in page_to_chunk if p > page]
        if high: return page_to_chunk[min(high)]
        return fallback

    def _source_metadata_key(self, meta: Dict[str, Any]) -> str:
        return str(meta.get("page") or "")

    def _percentile(self, values: List[float], p: int) -> float:
        if not values: return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, math.ceil(p / 100 * len(sorted_v)) - 1))
        return round(sorted_v[idx], 2)

    async def _prepare_cache_inputs(self, tasks: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str, str], Dict[str, Any]], Dict[str, int]]:
        prepared = {}
        stats = {"skipped_small": 0, "skipped_duplicate": 0}
        seen_hashes = set()
        
        for task in tasks:
            path = task.get("image_path")
            if not path: continue
            
            ctype = str(task.get("content_type", "table")).lower()
            meta = task.get("metadata") or {}
            key = (path, ctype, self._source_metadata_key(meta))
            
            try:
                img_bytes = await asyncio.to_thread(Path(path).read_bytes)
                if self._is_small_image(img_bytes):
                    stats["skipped_small"] += 1
                    continue
                
                h = ImageHasher.sha256_bytes(img_bytes)
                if (h, ctype) in seen_hashes:
                    stats["skipped_duplicate"] += 1
                    continue
                
                seen_hashes.add((h, ctype))
                prepared[key] = {"image_bytes": img_bytes, "image_hash": h, "content_type": ctype}
            except Exception:
                continue
        return prepared, stats

    def _is_small_image(self, data: bytes) -> bool:
        if len(data) < 16384: return True
        try:
            from PIL import Image
            with Image.open(BytesIO(data)) as img:
                return img.width < 200 or img.height < 200
        except: return False

    async def _prefetch_visual_cache(self, inputs: Dict[Tuple[str, str, str], Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        hashes = [str(v["image_hash"]) for v in inputs.values()]
        ctypes = [str(v["content_type"]) for v in inputs.values()]
        if not hashes: return {}
        
        settings_model = get_model_settings()
        rows = await self.visual_cache_repository.get_cached_extractions(
            hashes=hashes,
            content_types=ctypes,
            provider=settings_model.resolved_ingest_provider.value,
            model_name=settings_model.resolved_ingest_model_name,
            prompt_version=str(settings.VISUAL_CACHE_PROMPT_VERSION or "v1"),
            schema_version=str(settings.VISUAL_CACHE_SCHEMA_VERSION or "VisualParseResult:v1")
        )
        return {f"{r['image_hash']}:{r['content_type']}": r for r in rows}

    async def _parse_visual_entries_concurrently(self, candidate_entries: List[Dict[str, Any]], prefetched_cache: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(3)
        
        async def parse_one(entry: Dict[str, Any]) -> Dict[str, Any]:
            prep = entry["prepared"]
            cache_key = f"{prep['image_hash']}:{prep['content_type']}"
            
            if cache_key in prefetched_cache:
                return {
                    "entry": entry,
                    "parse_result": VisualParseResult.model_validate(prefetched_cache[cache_key]["result_data"]),
                    "cache_hit": True,
                    "parse_duration_ms": 0.0
                }

            async with semaphore:
                try:
                    res = await self.visual_parser.parse_image(
                        image_path=entry["image_path"],
                        image_bytes=prep["image_bytes"],
                        content_type=entry["content_type"],
                        source_metadata=entry["task_metadata"]
                    )
                    meta = res.visual_metadata or {}
                    return {
                        "entry": entry, "parse_result": res,
                        "cache_hit": meta.get("cache_hit", False),
                        "parse_duration_ms": meta.get("parse_duration_ms")
                    }
                except Exception as e:
                    return {"entry": entry, "parse_error": str(e)}

        return await asyncio.gather(*(parse_one(e) for e in candidate_entries))
