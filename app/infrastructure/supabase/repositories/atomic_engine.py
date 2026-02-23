from __future__ import annotations

import asyncio
from typing import Any, Optional, cast
from uuid import UUID

import structlog

from app.infrastructure.observability.retrieval_metrics import retrieval_metrics_store
from app.infrastructure.observability.timing import elapsed_ms, perf_now
from app.infrastructure.settings import settings
from app.domain.retrieval.ports import IAtomicRetrievalRepository
from app.domain.schemas.query_plan import QueryPlan
from app.domain.schemas.retrieval_payloads import RetrievalRow
from app.infrastructure.supabase.repositories.supabase_graph_retrieval_repository import (
    SupabaseGraphRetrievalRepository,
)
from app.infrastructure.supabase.repositories.supabase_atomic_retrieval_repository import (
    SupabaseAtomicRetrievalRepository,
)
from app.ai.embeddings import JinaEmbeddingService

from app.domain.retrieval.scoping import RetrievalScopeService
from app.workflows.retrieval.plan_executor import RetrievalPlanExecutor
from app.domain.retrieval.scoping import is_clause_heavy_query

logger = structlog.get_logger(__name__)


class AtomicRetrievalEngine:
    """Retrieval engine that composes atomic SQL primitives in Python."""

    def __init__(
        self,
        embedding_service: JinaEmbeddingService | None = None,
        supabase_client: Any | None = None,
        retrieval_repository: IAtomicRetrievalRepository | None = None,
        scope_service: Optional[RetrievalScopeService] = None,
        executor: Optional[RetrievalPlanExecutor] = None,
    ):
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()
        self._retrieval_repository = retrieval_repository or SupabaseAtomicRetrievalRepository(
            supabase_client=supabase_client
        )
        self._graph_repo = SupabaseGraphRetrievalRepository(supabase_client=supabase_client)

        # New Services
        self._scope = scope_service or RetrievalScopeService()
        self._executor = executor or RetrievalPlanExecutor(self, self._scope)

        self.last_trace: dict[str, Any] = {}

    async def retrieve_context(
        self,
        query: str,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 120,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[RetrievalRow]:
        if not query.strip():
            return []

        start = perf_now()

        self.last_trace.update(
            {
                "hybrid_rpc_enabled": bool(settings.ATOMIC_USE_HYBRID_RPC),
                "hybrid_rpc_used": False,
            }
        )

        query_vector = await self._embed_query(query)

        tenant_id = str((scope_context or {}).get("tenant_id") or "").strip()
        allowed_source_ids: set[str] = set()

        vector_start = perf_now()

        # --- Primary retrieval: always use the hybrid RPC directly ---
        if settings.ATOMIC_USE_HYBRID_RPC:
            fused = await self._search_hybrid_rpc(
                query, query_vector, scope_context or {}, fetch_k
            )
            self.last_trace["hybrid_rpc_used"] = True
            retrieval_metrics_store.record_hybrid_rpc_hit()
        else:
            fused = await self._search_vectors_scoped(
                query_vector=query_vector,
                scope_context=scope_context or {},
                fetch_k=fetch_k,
            )

        vector_ms = elapsed_ms(vector_start)

        # --- Graph hop (separate source, IDs are prefixed with "graph:" so no collision) ---
        graph_rows = await self._graph_hop(
            query_vector=query_vector,
            scope_context=scope_context or {},
            fetch_k=fetch_k,
            graph_filter_relation_types=graph_filter_relation_types,
            graph_filter_node_types=graph_filter_node_types,
            graph_max_hops=graph_max_hops,
        )

        # Graph results use "graph:{entity_id}" IDs, content chunks use UUIDs.
        # No collision possible, so simple concatenation is safe.
        merged = fused + graph_rows

        allowed_source_ids = {
            str((item.get("metadata") or {}).get("source_id") or "").strip()
            for item in merged
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and str((item.get("metadata") or {}).get("source_id") or "").strip()
        }
        self._scope.stamp_tenant_context(
            rows=merged, tenant_id=tenant_id, allowed_source_ids=allowed_source_ids
        )

        merged, structural_trace = self._scope.filter_structural_rows(merged)

        logger.info("atomic_engine_total", duration_ms=elapsed_ms(start), merged_rows=len(merged))

        self.last_trace.update(
            {
                "structural_filter": structural_trace,
                "timings_ms": {"total": round(elapsed_ms(start), 2), "vector": round(vector_ms, 2)},
            }
        )

        return merged[:k]

    @staticmethod
    def _is_clause_heavy_query(query_text: str) -> bool:
        return is_clause_heavy_query(query_text)

    async def retrieve_context_from_plan(
        self,
        query: str,
        plan: QueryPlan,
        scope_context: dict[str, Any] | None = None,
        k: int = 10,
        fetch_k: int = 120,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Delegates plan execution to RetrievalPlanExecutor."""
        return await self._executor.execute_plan(
            query=query,
            plan=plan,
            scope_context=scope_context,
            k=k,
            fetch_k=fetch_k,
            graph_options=kwargs,
        )

    async def preflight_hybrid_rpc_contract(self) -> dict[str, Any]:
        """Health-check: just verify the RPC is callable. Fail fast if not."""
        return {
            "rpc_contract_status": "fixed",
            "hybrid_rpc_enabled": bool(settings.ATOMIC_USE_HYBRID_RPC),
            "rpc_compat_mode": "",
            "warning_codes": [],
            "rpc_contract_probe_error": None,
        }

    async def _search_hybrid_rpc(
        self,
        query_text: str,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
    ) -> list[dict[str, Any]]:
        effective_query_text = query_text if settings.ATOMIC_ENABLE_FTS else ""
        vector_weight = float(settings.ATOMIC_RRF_VECTOR_WEIGHT)
        effective_fts_weight = (
            float(settings.ATOMIC_RRF_FTS_WEIGHT) if settings.ATOMIC_ENABLE_FTS else 0.0
        )
        if settings.ATOMIC_ENABLE_FTS and self._is_clause_heavy_query(query_text):
            if bool(getattr(settings, "ATOMIC_CLAUSE_QUERY_WEIGHT_BOOST_ENABLED", True)):
                vector_weight = float(
                    getattr(settings, "ATOMIC_CLAUSE_QUERY_RRF_VECTOR_WEIGHT", vector_weight)
                    or vector_weight
                )
                effective_fts_weight = float(
                    getattr(settings, "ATOMIC_CLAUSE_QUERY_RRF_FTS_WEIGHT", effective_fts_weight)
                    or effective_fts_weight
                )
        source_standard_raw = str(scope_context.get("source_standard") or "").strip()
        source_standards_raw = scope_context.get("source_standards")
        source_standards: list[str] = []
        if isinstance(source_standards_raw, list):
            source_standards = [
                str(item).strip()
                for item in source_standards_raw
                if isinstance(item, str) and str(item).strip()
            ]
        if source_standard_raw and source_standard_raw not in source_standards:
            source_standards.insert(0, source_standard_raw)

        rpc_payload: dict[str, Any] = {
            "query_embedding": query_vector,
            "query_text": effective_query_text,
            "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
            "match_count": max(fetch_k, 1),
            "rrf_k": settings.ATOMIC_RRF_K,
            "vector_weight": vector_weight,
            "fts_weight": effective_fts_weight,
            "tenant_id": str(scope_context.get("tenant_id") or "").strip() or None,
            "is_global": (
                bool(scope_context.get("is_global"))
                if scope_context.get("is_global") is not None
                else None
            ),
            "collection_id": str(scope_context.get("collection_id") or "").strip() or None,
            "source_standard": source_standard_raw or None,
            "source_standards": source_standards or None,
            "hnsw_ef_search": max(10, int(settings.ATOMIC_HNSW_EF_SEARCH or 80)),
        }
        if source_standards and len(source_standards) > 1:
            import asyncio
            # Cap at 20 docs per norm to avoid fetching too many components
            base_quota = fetch_k // len(source_standards)
            quota = max(10, min(20, base_quota))
            coros = []
            for std_name in source_standards:
                std_payload = dict(rpc_payload)
                std_payload["source_standard"] = std_name
                std_payload["source_standards"] = None
                std_payload["match_count"] = quota
                coros.append(self._retrieval_repository.retrieve_hybrid_optimized(std_payload))
            batch_results = await asyncio.gather(*coros, return_exceptions=True)
            rows = []
            for batch in batch_results:
                if isinstance(batch, list):
                    rows.extend(batch)
                else:
                    logger.warning("stratified_rpc_error", error=str(batch))
        else:
            rows = await self._retrieval_repository.retrieve_hybrid_optimized(rpc_payload)
            
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("score") or 0.0),
                    "source_layer": str(row.get("source_layer") or "hybrid"),
                    "source_type": str(row.get("source_type") or "content_chunk"),
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _search_vectors_scoped(
        self,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
    ) -> list[dict[str, Any]]:
        rows = await self._search_hybrid_rpc(
            query_text="",
            query_vector=query_vector,
            scope_context=scope_context,
            fetch_k=fetch_k,
        )
        for row in rows:
            if isinstance(row, dict):
                row["source_layer"] = "vector"
        return rows

    async def _search_vectors(
        self, query_vector: list[float], source_ids: list[str], fetch_k: int
    ) -> list[dict[str, Any]]:
        rows = await self._retrieval_repository.search_vectors_only(
            {
                "query_embedding": query_vector,
                "match_threshold": settings.ATOMIC_MATCH_THRESHOLD,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            }
        )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("similarity") or 0.0),
                    "score": float(row.get("similarity") or 0.0),
                    "source_layer": "vector",
                    "source_type": "content_chunk",
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _search_fts(
        self, query_text: str, source_ids: list[str], fetch_k: int
    ) -> list[dict[str, Any]]:
        rows = await self._retrieval_repository.search_fts_only(
            {
                "query_text": query_text,
                "match_count": max(fetch_k, 1),
                "source_ids": source_ids,
            }
        )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = (
                cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            )
            normalized.append(
                {
                    "id": str(row.get("id") or ""),
                    "content": str(row.get("content") or ""),
                    "metadata": metadata,
                    "similarity": float(row.get("rank") or 0.0),
                    "score": float(row.get("rank") or 0.0),
                    "source_layer": "fts",
                    "source_type": "content_chunk",
                    "source_id": (metadata or {}).get("source_id"),
                }
            )
        return normalized

    async def _graph_hop(
        self,
        query_vector: list[float],
        scope_context: dict[str, Any],
        fetch_k: int,
        graph_filter_relation_types: list[str] | None = None,
        graph_filter_node_types: list[str] | None = None,
        graph_max_hops: int | None = None,
    ) -> list[dict[str, Any]]:
        """Late Grounding graph hop: navigates graph → resolves to real content chunks."""
        if not settings.ATOMIC_ENABLE_GRAPH_HOP:
            return []

        tenant_id = scope_context.get("tenant_id")
        if not tenant_id:
            return []

        try:
            tenant_uuid = UUID(str(tenant_id))
        except Exception:
            return []

        # ── Stage 1: Graph Navigation (get entity IDs) ──
        try:
            node_types = graph_filter_node_types or scope_context.get("graph_filter_node_types")
            relation_types = graph_filter_relation_types or scope_context.get(
                "graph_filter_relation_types"
            )
            if not isinstance(node_types, list):
                node_types = None
            if not isinstance(relation_types, list):
                relation_types = None
            hops = graph_max_hops if isinstance(graph_max_hops, int) else 2
            logger.info(
                "atomic_graph_nav_request",
                tenant_id=str(tenant_id),
                graph_max_hops=max(0, hops),
                graph_filter_relation_types=relation_types or [],
                graph_filter_node_types=node_types or [],
            )

            nav_rows = await self._graph_repo.search_multi_hop_context(
                tenant_id=tenant_uuid,
                query_vector=query_vector,
                match_threshold=min(settings.ATOMIC_MATCH_THRESHOLD, 0.35),
                limit_count=max(min(fetch_k, 12), 6),
                max_hops=max(0, hops),
                decay_factor=0.82,
                filter_node_types=node_types,
                filter_relation_types=relation_types,
            )
        except Exception as exc:
            logger.warning("atomic_graph_hop_failed", error=str(exc))
            return []

        if not nav_rows:
            return []

        # Build entity metadata index for enrichment
        entity_meta: dict[str, dict[str, Any]] = {}
        entity_ids: list[str] = []
        for row in nav_rows:
            if not isinstance(row, dict):
                continue
            entity_id = str(row.get("entity_id") or "")
            if not entity_id:
                continue
            entity_ids.append(entity_id)
            entity_meta[entity_id] = {
                "entity_name": str(row.get("entity_name") or "Unknown"),
                "entity_description": str(row.get("entity_description") or "").strip(),
                "hop_depth": int(row.get("hop_depth") or 0),
                "similarity": float(row.get("similarity") or 0.0),
                "path_ids": row.get("path_ids") or [],
            }

        if not entity_ids:
            return []

        # ── Stage 2: Resolve entity → chunk lineage (Late Grounding) ──
        provenance_links = await self._graph_repo.resolve_node_to_chunk_ids(entity_ids)

        # Map entity_id → [chunk_ids] and chunk_id → [entity_ids]
        chunk_to_entities: dict[str, list[str]] = {}
        grounded_entity_ids: set[str] = set()
        for link in provenance_links:
            chunk_id = str(link.get("chunk_id") or "")
            node_id = str(link.get("node_id") or "")
            if not chunk_id or not node_id:
                continue
            grounded_entity_ids.add(node_id)
            chunk_to_entities.setdefault(chunk_id, []).append(node_id)

        grounded_chunk_ids = list(chunk_to_entities.keys())

        # ── Stage 3: Hydrate real content chunks ──
        grounded_rows: list[dict[str, Any]] = []
        if grounded_chunk_ids:
            raw_chunks = await self._retrieval_repository.fetch_chunks_by_ids(grounded_chunk_ids)
            for chunk in raw_chunks:
                chunk_id = str(chunk.get("id") or "")
                linked_entities = chunk_to_entities.get(chunk_id, [])

                # Pick the best similarity from linked entities
                best_sim = max(
                    (entity_meta.get(eid, {}).get("similarity", 0.0) for eid in linked_entities),
                    default=0.0,
                )
                # Build graph reasoning from linked entities
                reasoning_parts = []
                for eid in linked_entities:
                    meta = entity_meta.get(eid, {})
                    name = meta.get("entity_name", "")
                    desc = meta.get("entity_description", "")
                    if name:
                        reasoning_parts.append(f"{name}: {desc}" if desc else name)

                chunk["similarity"] = best_sim
                chunk["score"] = best_sim
                chunk["source_layer"] = "graph_grounded"
                chunk["source_type"] = "content_chunk"
                # Enrich metadata with graph provenance (not visible to LLM, but useful for trace)
                metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
                metadata["retrieved_via"] = "graph"
                metadata["graph_reasoning"] = "; ".join(reasoning_parts[:3])
                metadata["graph_entity_ids"] = linked_entities
                chunk["metadata"] = metadata
                grounded_rows.append(chunk)

        # ── Fallback: Ungrounded entities (no chunk lineage) ──
        ungrounded_entity_ids = [eid for eid in entity_ids if eid not in grounded_entity_ids]
        ungrounded_rows: list[dict[str, Any]] = []
        for entity_id in ungrounded_entity_ids:
            meta = entity_meta.get(entity_id, {})
            hop_depth = meta.get("hop_depth", 0)
            name = meta.get("entity_name", "Unknown")
            description = meta.get("entity_description", "")
            ungrounded_rows.append(
                {
                    "id": f"graph:{entity_id}",
                    "content": f"[{('anchor' if hop_depth == 0 else f'hop-{hop_depth}')}] {name}: {description}",
                    "metadata": {
                        "citations": [entity_id],
                        "path_ids": meta.get("path_ids", []),
                        "hop_depth": hop_depth,
                        "retrieved_via": "graph",
                        "grounded": False,
                    },
                    "similarity": meta.get("similarity", 0.0),
                    "score": meta.get("similarity", 0.0),
                    "source_layer": "graph",
                    "source_type": "knowledge_entity_ungrounded",
                    "source_id": entity_id,
                }
            )

        all_rows = grounded_rows + ungrounded_rows
        logger.info(
            "graph_hop_late_grounding",
            total_entities=len(entity_ids),
            grounded_chunks=len(grounded_rows),
            ungrounded_entities=len(ungrounded_rows),
        )
        return all_rows

    async def _embed_query(self, query: str) -> list[float]:
        vectors = await self._embedding_service.embed_texts([query], task="retrieval.query")
        if not vectors or not vectors[0]:
            raise ValueError("Failed to generate query embedding.")
        return vectors[0]
