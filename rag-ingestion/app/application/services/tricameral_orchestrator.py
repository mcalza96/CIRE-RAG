import asyncio
from enum import Enum
from typing import Any, Optional

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_llm
from app.domain.knowledge_schemas import (
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


class TricameralOrchestrator:
    """
    Hybrid GraphRAG orchestrator.

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

    async def _probe_specificity(self, intent: RetrievalIntent) -> int:
        if not intent.tenant_id:
            return 0

        try:
            anchors = await self.local_graph.find_anchor_nodes(intent.tenant_id, intent.query)
            return len(anchors)
        except Exception as exc:
            logger.warning("specificity_probe_failed", error=str(exc))
            return 0

    async def _classify_query_mode(self, intent: RetrievalIntent) -> QueryMode:
        query = intent.query.strip()
        if not query:
            return QueryMode.GENERAL

        broad_markers = {
            "overall", "general", "global", "common", "all", "tendencias", "patrones", "riesgos"
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

    async def _safe_graph_search(self, intent: RetrievalIntent) -> list[dict[str, Any]]:
        """Deprecated legacy constraints path; kept for compatibility."""
        tags = (intent.metadata or {}).get("context_tags") or []
        if not tags or not self.graph_service:
            return []

        try:
            constraints = await self.graph_service.get_active_constraints(tags)
            return constraints if isinstance(constraints, list) else []
        except Exception as exc:
            logger.warning("legacy_constraint_search_failed", error=str(exc))
            return []

    async def _retrieve_raptor(self, intent: RetrievalIntent, limit: int = 8) -> list[dict[str, Any]]:
        if not intent.tenant_id:
            return []

        collection_id = None
        if isinstance(intent.metadata, dict):
            raw_collection = intent.metadata.get("collection_id")
            if raw_collection:
                collection_id = str(raw_collection)

        if self.raptor_service and hasattr(self.raptor_service, "search_summaries"):
            try:
                results = await self.raptor_service.search_summaries(intent.query, intent.tenant_id, limit=limit)
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
        scope_context = self._scope_from_intent(intent)
        mode = await self._classify_query_mode(intent)

        logger.info("tricameral_route_selected", mode=mode.value, tenant_id=intent.tenant_id)

        tasks: list = []
        labels: list[str] = []

        should_vector = mode in {QueryMode.SPECIFIC, QueryMode.HYBRID}
        should_local = mode in {QueryMode.SPECIFIC, QueryMode.HYBRID}
        should_global = mode in {QueryMode.GENERAL, QueryMode.HYBRID}
        should_raptor = mode in {QueryMode.GENERAL, QueryMode.HYBRID}

        if should_vector:
            tasks.append(self.vector_tools.retrieve(query=intent.query, scope_context=scope_context, k=max(k, 15)))
            labels.append("vector")

        if should_local and intent.tenant_id:
            tasks.append(self.local_graph.search(query=intent.query, tenant_id=intent.tenant_id))
            labels.append("local")

        if should_global and intent.tenant_id:
            tasks.append(self.global_graph.search(query=intent.query, tenant_id=intent.tenant_id, top_k=5))
            labels.append("global")

        if should_raptor:
            tasks.append(self._retrieve_raptor(intent, limit=8))
            labels.append("raptor")

        responses = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        bundle = {label: resp for label, resp in zip(labels, responses)}

        vector_results: list[dict[str, Any]] = []
        graph_context_results: list[dict[str, Any]] = []
        raptor_results: list[dict[str, Any]] = []
        fallback_triggered = False

        for label, payload in bundle.items():
            if isinstance(payload, Exception):
                logger.warning("tricameral_source_failed", source=label, error=str(payload))
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
            vector_results = await self.vector_tools.retrieve(query=intent.query, scope_context=scope_context, k=max(k, 15))

        merged = self._dedupe_results(vector_results + graph_context_results + raptor_results)
        if not merged:
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

        ranked = self.reranker.rerank(rag_candidates, rerank_intent)
        top_ranked = ranked[:k]

        context = "\n\n".join(item.content for item in top_ranked if item.content)
        citations: list[str] = []
        for item in top_ranked:
            citations.extend(item.metadata.get("citations", []))
            if item.id:
                citations.append(item.id)

        return {
            "mode": mode.value,
            "results": [item.model_dump() for item in top_ranked],
            "context": context,
            "citations": list(dict.fromkeys(citations)),
        }
