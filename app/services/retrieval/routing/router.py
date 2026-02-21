import asyncio
import re
import time
from enum import Enum
from typing import Any, Optional
from uuid import UUID

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from app.ai.llm import get_llm
from app.infrastructure.settings import settings
from app.infrastructure.observability.scope_metrics import scope_metrics_store
from app.domain.schemas.query_plan import PlannedSubQuery, QueryPlan
from app.domain.schemas.knowledge_schemas import (
    AgentRole,
    RAGSearchResult,
    RetrievalIntent,
    TaskType,
)
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.graph_retrieval_strategies import LocalGraphSearch, GlobalGraphSearch
from app.services.knowledge.gravity_reranker import GravityReranker

logger = structlog.get_logger(__name__)


class QueryMode(str, Enum):
    SPECIFIC = "SPECIFIC"
    GENERAL = "GENERAL"
    HYBRID = "HYBRID"


class RetrievalRouter:
    """
    Hybrid GraphRAG retrieval router.

    Router pattern:
    - SPECIFIC -> Vector + LocalGraph
    - GENERAL  -> GlobalGraph + RAPTOR summaries
    - HYBRID   -> all sources
    """

    def __init__(
        self,
        vector_tools,
        graph_service: Optional[Any] = None,
        raptor_service: Optional[Any] = None,
        reranker: Optional[GravityReranker] = None,
        supabase_client=None,
        llm_provider: Optional[BaseChatModel] = None,
        embedding_service: Optional[JinaEmbeddingService] = None,
    ):
        self.vector_tools = vector_tools
        self.graph_service = graph_service
        self.raptor_service = raptor_service
        self.reranker = reranker or GravityReranker()
        self._supabase = supabase_client
        self._llm = llm_provider or get_llm(temperature=0.0, capability="FORENSIC")
        self._embedding = embedding_service or JinaEmbeddingService.get_instance()

        self.local_graph = LocalGraphSearch(
            supabase_client=supabase_client,
            llm_provider=self._llm,
            embedding_service=self._embedding,
        )
        self.global_graph = GlobalGraphSearch(
            supabase_client=supabase_client,
            embedding_service=self._embedding,
        )

    async def _get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    @staticmethod
    def _scope_from_intent(intent: RetrievalIntent) -> dict[str, Any]:
        if intent.tenant_id:
            return {"type": "institutional", "tenant_id": intent.tenant_id}
        return {"type": "global"}

    @staticmethod
    def _extract_requested_scopes(intent: RetrievalIntent) -> tuple[str, ...]:
        metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
        values: list[str] = []

        raw = metadata.get("requested_scopes")
        if isinstance(raw, list):
            values.extend(str(v).strip() for v in raw if isinstance(v, str) and str(v).strip())

        if not values:
            seen: set[str] = set()
            for match in re.findall(
                r"\biso\s*[-:]?\s*(\d{4,5})\b", intent.query or "", flags=re.IGNORECASE
            ):
                item = f"ISO {match}"
                if item in seen:
                    continue
                seen.add(item)
                values.append(item)

        return tuple(dict.fromkeys(values))

    @staticmethod
    def _extract_item_scope(item: dict[str, Any]) -> str:
        meta_raw = item.get("metadata")
        metadata: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
        candidates = [
            metadata.get("source_standard"),
            metadata.get("standard"),
            metadata.get("scope"),
            metadata.get("norma"),
            item.get("source_standard"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        return ""

    def _apply_scope_penalty(
        self, items: list[dict[str, Any]], requested_scopes: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        if not requested_scopes:
            return items

        requested_upper = {scope.upper() for scope in requested_scopes}
        scored: list[dict[str, Any]] = []
        for item in items:
            row_scope = self._extract_item_scope(item)
            if not row_scope or any(scope in row_scope for scope in requested_upper):
                scored.append(item)
                continue

            patched = dict(item)
            base = float(patched.get("score", patched.get("similarity", 0.0)) or 0.0)
            penalized = max(base * 0.25, 0.0)
            patched["scope_penalized"] = True
            patched["scope_penalty"] = 0.75
            patched["score"] = penalized
            patched["similarity"] = penalized
            scored.append(patched)

        return scored

    async def _probe_specificity(self, intent: RetrievalIntent) -> int:
        tenant_uuid = self._tenant_uuid(intent.tenant_id)
        if not tenant_uuid:
            return 0

        try:
            anchors = await self.local_graph.find_anchor_nodes(tenant_uuid, intent.query)
            return len(anchors)
        except Exception as exc:
            logger.warning("specificity_probe_failed", error=str(exc))
            return 0

    @staticmethod
    def _tenant_uuid(tenant_id: Optional[str]) -> Optional[UUID]:
        if not tenant_id:
            return None
        try:
            return UUID(str(tenant_id))
        except Exception:
            return None

    async def _classify_query_mode(self, intent: RetrievalIntent) -> QueryMode:
        query = intent.query.strip()
        if not query:
            return QueryMode.GENERAL

        broad_markers = {
            "overall",
            "general",
            "global",
            "common",
            "all",
            "tendencias",
            "patrones",
            "riesgos",
        }
        lowered = query.lower()
        broad_hit = any(token in lowered for token in broad_markers)
        specificity_count = await self._probe_specificity(intent)

        if specificity_count >= 2 and not broad_hit:
            return QueryMode.SPECIFIC
        if specificity_count == 0 and broad_hit:
            return QueryMode.GENERAL

        router_prompt = (
            "Classify this query for retrieval routing. "
            "Output only one label: SPECIFIC, GENERAL, or HYBRID. "
            "SPECIFIC = asks about a concrete entity/case. "
            "GENERAL = asks for broad patterns/themes. "
            "HYBRID = combines specific and broad intent."
        )

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": router_prompt},
                    {"role": "user", "content": query},
                ]
            )
            label = str(response.content).strip().upper()
            if "SPECIFIC" in label:
                return QueryMode.SPECIFIC
            if "GENERAL" in label:
                return QueryMode.GENERAL
            return QueryMode.HYBRID
        except Exception:
            if specificity_count > 0:
                return QueryMode.HYBRID
            return QueryMode.GENERAL

    async def _retrieve_raptor(
        self, intent: RetrievalIntent, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not intent.tenant_id:
            return []

        collection_id = None
        if isinstance(intent.metadata, dict):
            raw_collection = intent.metadata.get("collection_id")
            if raw_collection:
                collection_id = str(raw_collection)

        if self.raptor_service and hasattr(self.raptor_service, "search_summaries"):
            try:
                results = await self.raptor_service.search_summaries(
                    intent.query, intent.tenant_id, limit=limit
                )
                if isinstance(results, list):
                    return [r.model_dump() if hasattr(r, "model_dump") else r for r in results]
            except Exception as exc:
                logger.warning("raptor_service_failed", error=str(exc))

        if self.vector_tools and hasattr(self.vector_tools, "retrieve_summaries"):
            try:
                return await self.vector_tools.retrieve_summaries(
                    intent.query,
                    intent.tenant_id,
                    k=limit,
                    collection_id=collection_id,
                )
            except Exception as exc:
                logger.warning("raptor_tools_fallback_failed", error=str(exc))
        return []

    async def _retrieve_from_query_plan(
        self,
        plan: QueryPlan,
        scope_context: dict[str, Any],
        k: int,
    ) -> list[dict[str, Any]]:
        if not plan.sub_queries:
            return []

        if plan.execution_mode == "sequential":
            merged: list[dict[str, Any]] = []
            for sq in plan.sub_queries:
                rows = await self.vector_tools.retrieve(
                    query=sq.query,
                    scope_context=scope_context,
                    k=max(6, min(k, 12)),
                )
                merged.extend(rows or [])
            return self._dedupe_results(merged)

        tasks = [
            self.vector_tools.retrieve(
                query=sq.query,
                scope_context=scope_context,
                k=max(6, min(k, 12)),
            )
            for sq in plan.sub_queries
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict[str, Any]] = []
        for payload in responses:
            if isinstance(payload, Exception):
                logger.warning("planned_subquery_failed", error=str(payload))
                continue
            if isinstance(payload, list):
                merged.extend(payload)
        return self._dedupe_results(merged)

    @staticmethod
    def _dedupe_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("id") or item.get("source_id") or "")
            content = str(item.get("content", ""))
            key = item_id or content[:120]
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _build_graph_result(
        self,
        content: str,
        source_id: str,
        source_layer: str,
        citations: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "id": source_id,
            "content": content,
            "similarity": 0.82,
            "score": 0.82,
            "source_layer": source_layer,
            "source_id": source_id,
            "metadata": {
                "authority_level": "supplementary",
                "citations": citations or [],
                **(metadata or {}),
            },
        }

    async def orchestrate(self, intent: RetrievalIntent, k: int = 20) -> dict[str, Any]:
        total_start = time.perf_counter()
        retrieval_request_id = str((intent.metadata or {}).get("retrieval_request_id") or "")
        requested_scopes = self._extract_requested_scopes(intent)

        scope_context = self._scope_from_intent(intent)
        planner_start = time.perf_counter()
        plan = self._plan_from_intent(intent)
        planner_used = bool(plan.sub_queries)
        planner_skipped_reason: str | None = None if planner_used else "no_external_plan"
        planner_ms = round((time.perf_counter() - planner_start) * 1000, 2)

        if plan.is_multihop:
            mode = QueryMode.HYBRID
            classify_ms = 0.0
        else:
            classify_start = time.perf_counter()
            mode = await self._classify_query_mode(intent)
            classify_ms = round((time.perf_counter() - classify_start) * 1000, 2)

        logger.info(
            "retrieval_router_route_selected",
            mode=mode.value,
            tenant_id=intent.tenant_id,
            retrieval_request_id=retrieval_request_id,
            classify_duration_ms=classify_ms,
            planner_duration_ms=planner_ms,
            planner_used=planner_used,
            planner_multihop=plan.is_multihop,
            planner_execution_mode=plan.execution_mode,
            planner_fallback_reason=plan.fallback_reason,
            planner_skipped_reason=planner_skipped_reason,
        )

        tasks: list = []
        labels: list[str] = []

        should_vector = mode in {QueryMode.SPECIFIC, QueryMode.HYBRID}
        should_local = mode in {QueryMode.SPECIFIC, QueryMode.HYBRID}
        should_global = mode in {QueryMode.GENERAL, QueryMode.HYBRID}
        should_raptor = mode in {QueryMode.GENERAL, QueryMode.HYBRID}
        tenant_uuid = self._tenant_uuid(intent.tenant_id)

        if should_vector and plan.is_multihop:
            tasks.append(
                self._retrieve_from_query_plan(plan=plan, scope_context=scope_context, k=max(k, 15))
            )
            labels.append("vector")
        elif should_vector:
            tasks.append(
                self.vector_tools.retrieve(
                    query=intent.query, scope_context=scope_context, k=max(k, 15)
                )
            )
            labels.append("vector")

        if should_local and tenant_uuid:
            tasks.append(self.local_graph.search(query=intent.query, tenant_id=tenant_uuid))
            labels.append("local")

        if should_global and tenant_uuid:
            tasks.append(
                self.global_graph.search(query=intent.query, tenant_id=tenant_uuid, top_k=5)
            )
            labels.append("global")

        if should_raptor:
            tasks.append(self._retrieve_raptor(intent, limit=8))
            labels.append("raptor")

        gather_start = time.perf_counter()
        responses = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        gather_ms = round((time.perf_counter() - gather_start) * 1000, 2)
        bundle = {label: resp for label, resp in zip(labels, responses)}

        vector_results: list[dict[str, Any]] = []
        graph_context_results: list[dict[str, Any]] = []
        raptor_results: list[dict[str, Any]] = []
        fallback_triggered = False

        for label, payload in bundle.items():
            if isinstance(payload, Exception):
                logger.warning("retrieval_router_source_failed", source=label, error=str(payload))
                continue

            if label == "vector" and isinstance(payload, list):
                vector_results = payload
            elif label == "local" and isinstance(payload, dict):
                if payload.get("found") and payload.get("context"):
                    graph_context_results.append(
                        self._build_graph_result(
                            content=payload["context"],
                            source_id="local_graph_context",
                            source_layer="graph_local",
                            citations=payload.get("citations", []),
                            metadata={"anchors": payload.get("anchors", [])},
                        )
                    )
                elif mode in {QueryMode.SPECIFIC, QueryMode.HYBRID}:
                    fallback_triggered = True
            elif label == "global" and isinstance(payload, dict):
                if payload.get("context"):
                    graph_context_results.append(
                        self._build_graph_result(
                            content=payload["context"],
                            source_id="global_graph_context",
                            source_layer="graph_global",
                            citations=payload.get("citations", []),
                            metadata={"community_ids": payload.get("community_ids", [])},
                        )
                    )
            elif label == "raptor" and isinstance(payload, list):
                raptor_results = payload

        if fallback_triggered and not vector_results:
            logger.info("local_graph_empty_fallback_to_vector", query=intent.query)
            vector_results = await self.vector_tools.retrieve(
                query=intent.query, scope_context=scope_context, k=max(k, 15)
            )

        merged = self._dedupe_results(vector_results + graph_context_results + raptor_results)
        if requested_scopes:
            merged = self._apply_scope_penalty(merged, requested_scopes)
            scope_penalized_count = sum(1 for item in merged if bool(item.get("scope_penalized")))
            scope_metrics_store.record_rerank_penalized(
                tenant_id=intent.tenant_id,
                penalized_count=scope_penalized_count,
                candidate_count=len(merged),
            )
            if settings.SCOPE_STRICT_FILTERING:
                strict_filtered = [item for item in merged if not bool(item.get("scope_penalized"))]
                if strict_filtered:
                    merged = strict_filtered
        else:
            scope_penalized_count = 0
        logger.info(
            "retrieval_pipeline_timing",
            stage="retrieval_router_sources",
            retrieval_request_id=retrieval_request_id,
            tenant_id=intent.tenant_id,
            duration_ms=gather_ms,
            mode=mode.value,
            source_counts={
                "vector": len(vector_results),
                "graph": len(graph_context_results),
                "raptor": len(raptor_results),
                "merged": len(merged),
            },
            requested_scopes=list(requested_scopes),
            scope_penalized_count=scope_penalized_count,
            fallback_triggered=fallback_triggered,
        )

        if not merged:
            logger.info(
                "retrieval_pipeline_timing",
                stage="retrieval_router_total",
                retrieval_request_id=retrieval_request_id,
                tenant_id=intent.tenant_id,
                duration_ms=round((time.perf_counter() - total_start) * 1000, 2),
                mode=mode.value,
                result_count=0,
            )
            return {
                "mode": mode.value,
                "results": [],
                "context": "",
                "citations": [],
            }

        rag_candidates = [
            RAGSearchResult(
                id=str(item.get("id", "")),
                content=str(item.get("content", "")),
                metadata=item.get("metadata", {}) or {},
                similarity=float(item.get("similarity", item.get("score", 0.0)) or 0.0),
                score=float(item.get("score", item.get("similarity", 0.0)) or 0.0),
                source_layer=str(item.get("source_layer", "knowledge")),
                source_id=item.get("source_id"),
            )
            for item in merged
        ]

        rerank_intent = RetrievalIntent(
            query=intent.query,
            role=intent.role or AgentRole.SOCRATIC_MENTOR,
            task=intent.task or TaskType.EXPLANATION,
            tenant_id=intent.tenant_id,
            metadata=intent.metadata,
        )

        rerank_start = time.perf_counter()
        ranked = self.reranker.rerank(rag_candidates, rerank_intent)
        rerank_ms = round((time.perf_counter() - rerank_start) * 1000, 2)
        top_ranked = ranked[:k]

        context = "\n\n".join(item.content for item in top_ranked if item.content)
        citations: list[str] = []
        for item in top_ranked:
            citations.extend(item.metadata.get("citations", []))
            if item.id:
                citations.append(item.id)

        total_ms = round((time.perf_counter() - total_start) * 1000, 2)
        logger.info(
            "retrieval_pipeline_timing",
            stage="retrieval_router_total",
            retrieval_request_id=retrieval_request_id,
            tenant_id=intent.tenant_id,
            duration_ms=total_ms,
            mode=mode.value,
            rerank_duration_ms=rerank_ms,
            result_count=len(top_ranked),
        )

        return {
            "mode": mode.value,
            "results": [item.model_dump() for item in top_ranked],
            "context": context,
            "citations": list(dict.fromkeys(citations)),
        }

    @staticmethod
    def _plan_from_intent(intent: RetrievalIntent) -> QueryPlan:
        metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
        raw_plan = metadata.get("retrieval_plan")
        if not isinstance(raw_plan, dict):
            return QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])

        raw_subqueries = raw_plan.get("sub_queries")
        if not isinstance(raw_subqueries, list):
            return QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])

        subqueries: list[PlannedSubQuery] = []
        for idx, item in enumerate(raw_subqueries, start=1):
            if not isinstance(item, dict):
                continue
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            raw_id = item.get("id")
            if isinstance(raw_id, int):
                sq_id = raw_id
            elif isinstance(raw_id, str) and raw_id.strip().isdigit():
                sq_id = int(raw_id.strip())
            else:
                sq_id = idx
            rels = item.get("target_relations")
            nodes = item.get("target_node_types")
            subqueries.append(
                PlannedSubQuery(
                    id=sq_id,
                    query=query,
                    dependency_id=(
                        item.get("dependency_id")
                        if isinstance(item.get("dependency_id"), int)
                        else None
                    ),
                    target_relations=[str(v).strip() for v in rels if str(v).strip()]
                    if isinstance(rels, list)
                    else None,
                    target_node_types=[str(v).strip() for v in nodes if str(v).strip()]
                    if isinstance(nodes, list)
                    else None,
                    is_deep=bool(item.get("is_deep", False)),
                )
            )

        if not subqueries:
            return QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])

        mode = str(raw_plan.get("execution_mode") or "parallel").strip().lower()
        return QueryPlan(
            is_multihop=bool(raw_plan.get("is_multihop", len(subqueries) > 1)),
            execution_mode="sequential" if mode == "sequential" else "parallel",
            sub_queries=subqueries,
            fallback_reason=str(raw_plan.get("fallback_reason") or "").strip() or None,
        )
