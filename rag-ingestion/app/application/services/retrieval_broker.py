import structlog
import time
import re
from typing import List, Dict, Any, Optional
from app.application.services.query_decomposer import QueryDecomposer, QueryPlan
from app.core.settings import settings
from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.gravity_reranker import GravityReranker
from app.services.knowledge.jina_reranker import JinaReranker
from app.services.ingestion.metadata_enricher import enrich_metadata
from app.core.observability.forensic import ForensicRecorder
from app.core.observability.scope_metrics import scope_metrics_store
from app.domain.knowledge_schemas import RAGSearchResult, RetrievalIntent, AgentRole, TaskType
from app.domain.interfaces.retrieval_interface import IRetrievalRepository
from app.services.knowledge.retrieval_strategies import DirectRetrievalStrategy, IterativeRetrievalStrategy
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine

logger = structlog.get_logger(__name__)

class RetrievalBroker:
    """
    Central orchestrator for knowledge retrieval.
    Decouples DSPy tools from business logic and infrastructure.
    """
    
    def __init__(self, repository: IRetrievalRepository):
        self.repository = repository
        self.reranker = GravityReranker()
        self.jina_reranker = JinaReranker()
        self.direct_strategy = DirectRetrievalStrategy(repository)
        self.iterative_strategy = IterativeRetrievalStrategy(repository)
        self.atomic_engine = AtomicRetrievalEngine()
        self.query_decomposer = QueryDecomposer()

    @staticmethod
    def _engine_mode() -> str:
        mode = str(settings.RETRIEVAL_ENGINE_MODE or "hybrid").strip().lower()
        if mode == "unified":
            return "hybrid"
        if mode not in {"atomic", "hybrid"}:
            return "hybrid"
        return mode

    @staticmethod
    def _extract_requested_standards(query: str) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for match in re.findall(r"\biso\s*[-:]?\s*(\d{4,5})\b", (query or ""), flags=re.IGNORECASE):
            value = f"ISO {match}"
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return tuple(ordered)

    @staticmethod
    def _extract_row_scope(item: Dict[str, Any]) -> str:
        meta_raw = item.get("metadata")
        metadata: Dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
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

    @staticmethod
    def _requested_scopes_from_context(scope_context: Dict[str, Any]) -> tuple[str, ...]:
        if not isinstance(scope_context, dict):
            return ()

        nested = scope_context.get("filters") if isinstance(scope_context.get("filters"), dict) else {}
        raw_list = scope_context.get("source_standards")
        if not raw_list and nested:
            raw_list = nested.get("source_standards")

        values: list[str] = []
        if isinstance(raw_list, list):
            values.extend(str(v).strip() for v in raw_list if isinstance(v, str) and str(v).strip())

        single = scope_context.get("source_standard") or (nested.get("source_standard") if nested else None)
        if isinstance(single, str) and single.strip():
            values.append(single.strip())

        if not values:
            return ()
        return tuple(dict.fromkeys(values))

    def _apply_scope_penalty(self, results: List[Dict[str, Any]], requested_scopes: tuple[str, ...]) -> List[Dict[str, Any]]:
        if not requested_scopes:
            return results

        requested_upper = {item.upper() for item in requested_scopes}
        reranked: list[Dict[str, Any]] = []
        for row in results:
            row_scope = self._extract_row_scope(row)
            if not row_scope:
                reranked.append(row)
                continue

            if any(scope in row_scope for scope in requested_upper):
                reranked.append(row)
                continue

            adjusted = dict(row)
            base_similarity = float(adjusted.get("jina_relevance_score", adjusted.get("similarity", adjusted.get("score", 0.0))) or 0.0)
            penalized = max(base_similarity * 0.25, 0.0)
            adjusted["scope_penalized"] = True
            adjusted["scope_penalty"] = 0.75
            adjusted["similarity"] = penalized
            adjusted["score"] = penalized
            adjusted["jina_relevance_score"] = penalized
            reranked.append(adjusted)

        return reranked

    @staticmethod
    def _count_scope_penalized(results: List[Dict[str, Any]]) -> int:
        return sum(1 for item in results if bool(item.get("scope_penalized")))

    async def retrieve(
        self, 
        query: str, 
        scope_context: Dict[str, Any], 
        k: int = 20,
        fetch_k: int = 50,
        enable_reranking: bool = True,
        iterative: bool = False,
        **strategy_kwargs
    ) -> List[Dict[str, Any]]:
        """
        Main entry point for retrieval orchestration.
        """
        if not query:
            return []

        # 1. Scope & Filter Resolution
        filter_conditions = self._resolve_filters(query, scope_context)
        
        try:
            # 2. Strategy Selection & Execution
            if iterative:
                engine = JinaEmbeddingService.get_instance()
                vectors = await engine.embed_texts([query], task="retrieval.query")
                if not vectors or not vectors[0]:
                    return []
                vector = vectors[0]

                strategy = self.iterative_strategy
                logger.info(
                    "retrieval_strategy_execution",
                    query=query,
                    strategy=strategy.__class__.__name__,
                    filters=filter_conditions,
                )

                raw_results = await strategy.execute(
                    query_vector=vector,
                    query_text=query,
                    filter_conditions=filter_conditions,
                    k=k,
                    fetch_k=fetch_k,
                    **strategy_kwargs,
                )
            else:
                mode = self._engine_mode()
                planner_used = bool(settings.QUERY_DECOMPOSER_ENABLED and mode in {"atomic", "hybrid"})
                if planner_used:
                    plan = await self.query_decomposer.decompose(query)
                else:
                    plan = QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])

                logger.info(
                    "retrieval_strategy_execution",
                    query=query,
                    strategy="AtomicRetrievalEngine",
                    filters=filter_conditions,
                    retrieval_engine_mode=mode,
                    planner_used=planner_used,
                    planner_multihop=plan.is_multihop,
                )

                raw_results = []
                try:
                    if plan.is_multihop:
                        raw_results = await self.atomic_engine.retrieve_context_from_plan(
                            query=query,
                            plan=plan,
                            scope_context=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                        )
                    else:
                        raw_results = await self.atomic_engine.retrieve_context(
                            query=query,
                            scope_context=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                        )
                except Exception as atomic_exc:
                    logger.warning("atomic_engine_failed", error=str(atomic_exc), mode=mode)
                    if mode == "atomic":
                        raise atomic_exc

                if not raw_results and mode == "hybrid":
                    engine = JinaEmbeddingService.get_instance()
                    vectors = await engine.embed_texts([query], task="retrieval.query")
                    if vectors and vectors[0]:
                        logger.info(
                            "retrieval_strategy_fallback",
                            query=query,
                            strategy="DirectRetrievalStrategy",
                            reason="atomic_empty_or_failed",
                        )
                        raw_results = await self.direct_strategy.execute(
                            query_vector=vectors[0],
                            query_text=query,
                            filter_conditions=filter_conditions,
                            k=k,
                            fetch_k=fetch_k,
                            **strategy_kwargs,
                        )

            if not raw_results:
                ForensicRecorder.record_retrieval(query, [], {"scope": scope_context.get("type"), "filters": filter_conditions})
                return []

            ForensicRecorder.record_retrieval(query, raw_results, {"scope": scope_context.get("type"), "filters": filter_conditions})

            # 4. Reranking Phase
            if enable_reranking and raw_results:
                return await self._apply_reranking(query, raw_results, filter_conditions, k)

            return raw_results[:k]

        except Exception as e:
            logger.error("broker_retrieval_failed", error=str(e), query=query)
            raise e

    async def retrieve_summaries(
        self,
        query: str,
        tenant_id: str,
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves RAPTOR summaries via repository."""
        if not query or not tenant_id:
            return []

        try:
            engine = JinaEmbeddingService.get_instance()
            vectors = await engine.embed_texts([query], task="retrieval.query")
            if not vectors or not vectors[0]:
                return []
            
            data = await self.repository.match_summaries(vectors[0], tenant_id, k, collection_id=collection_id)
            ForensicRecorder.record_retrieval(
                query,
                data,
                {"scope": "raptor_summaries", "tenant_id": tenant_id, "collection_id": collection_id},
            )
            return data
        except Exception as e:
            logger.error("raptor_retrieval_failed", error=str(e))
            return []

    def _resolve_filters(self, query: str, scope_context: Dict[str, Any]) -> Dict[str, Any]:
        scope_type = scope_context.get("type", "institutional")
        filters = {}

        if scope_type == "institutional":
            tenant_id = scope_context.get("tenant_id")
            if not tenant_id:
                raise ValueError("Institutional scope requires 'tenant_id'.")
            filters["tenant_id"] = tenant_id
        elif scope_type == "global":
            filters["is_global"] = True
        
        # Query Enrichment
        _, query_filters = enrich_metadata(query, {})
        if query_filters:
            filters["filters"] = query_filters

        # Merge extra filters
        extra = scope_context.get("filters", {})
        if extra:
            filters.update(extra)

        scope_collection = scope_context.get("collection_id")
        if scope_collection and not filters.get("collection_id"):
            filters["collection_id"] = scope_collection

        requested_standards = self._extract_requested_standards(query)
        if requested_standards:
            if not filters.get("source_standards"):
                filters["source_standards"] = list(requested_standards)
            if not filters.get("source_standard"):
                filters["source_standard"] = requested_standards[0]

        if not filters.get("source_standards"):
            context_scopes = self._requested_scopes_from_context(scope_context)
            if context_scopes:
                filters["source_standards"] = list(context_scopes)
                filters["source_standard"] = context_scopes[0]
              
        return filters

    async def _apply_reranking(self, query: str, results: List[Dict], scope_context: Dict, k: int) -> List[Dict]:
        try:
            semantic_ranked = results
            requested_scopes = self._requested_scopes_from_context(scope_context)
            jina_started = time.perf_counter()
            if self.jina_reranker.is_enabled() and results:
                docs = [str(item.get("content") or "") for item in results]
                rows = await self.jina_reranker.rerank_documents(
                    query=query,
                    documents=docs,
                    top_n=max(k, min(len(results), 40)),
                )
                if rows:
                    reordered: list[Dict[str, Any]] = []
                    for row in rows:
                        idx = row.get("index")
                        if not isinstance(idx, int) or idx < 0 or idx >= len(results):
                            continue
                        source = dict(results[idx])
                        source["jina_relevance_score"] = float(row.get("relevance_score") or 0.0)
                        reordered.append(source)
                    if reordered:
                        semantic_ranked = reordered

            if requested_scopes:
                semantic_ranked = self._apply_scope_penalty(semantic_ranked, requested_scopes)
            candidate_count = len(semantic_ranked)
            scope_penalized_count = self._count_scope_penalized(semantic_ranked)
            tenant_id = str(scope_context.get("tenant_id") or "")
            scope_metrics_store.record_rerank_penalized(
                tenant_id=tenant_id,
                penalized_count=scope_penalized_count,
                candidate_count=candidate_count,
            )

            if requested_scopes and settings.SCOPE_STRICT_FILTERING:
                strict_filtered = [item for item in semantic_ranked if not bool(item.get("scope_penalized"))]
                if strict_filtered:
                    semantic_ranked = strict_filtered

            scope_penalized_ratio = round(
                (scope_penalized_count / candidate_count) if candidate_count else 0.0,
                4,
            )

            logger.info(
                "retrieval_pipeline_timing",
                stage="jina_rerank",
                duration_ms=round((time.perf_counter() - jina_started) * 1000, 2),
                enabled=self.jina_reranker.is_enabled(),
                candidates=len(results),
                ranked=len(semantic_ranked),
                requested_scopes=list(requested_scopes),
                scope_penalized_count=scope_penalized_count,
                scope_penalized_ratio=scope_penalized_ratio,
            )

            raw_by_id = {str(item.get("id", "")): item for item in results}
            candidates = [
                RAGSearchResult(
                    id=str(item.get("id", "")),
                    content=item.get("content", ""),
                    metadata=item.get("metadata", {}),
                    similarity=float(item.get("jina_relevance_score", item.get("similarity", 0.0)) or 0.0),
                    score=float(item.get("jina_relevance_score", item.get("similarity", 0.0)) or 0.0),
                    source_layer="knowledge",
                    source_id=item.get("source_id")
                ) for item in semantic_ranked
            ]
            
            intent = RetrievalIntent(
                query=query,
                role=AgentRole(scope_context.get("role", "socratic_mentor").lower()),
                task=TaskType.EXPLANATION 
            )
            
            ranked = self.reranker.rerank(candidates, intent)
            merged: List[Dict[str, Any]] = []
            for rc in ranked[:k]:
                payload = rc.model_dump()
                source = raw_by_id.get(str(rc.id), {})
                if "is_visual_anchor" in source:
                    payload["is_visual_anchor"] = bool(source.get("is_visual_anchor"))
                if "parent_chunk_id" in source:
                    payload["parent_chunk_id"] = source.get("parent_chunk_id")
                if "source_type" in source:
                    payload["source_type"] = source.get("source_type")
                merged.append(payload)
            return merged
        except Exception as e:
            logger.warning("reranking_fallback", error=str(e))
            return results[:k]
