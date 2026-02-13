import structlog
import time
from typing import List, Dict, Any, Optional
from app.application.services.query_decomposer import QueryDecomposer, QueryPlan
from app.core.settings import settings
from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.gravity_reranker import GravityReranker
from app.services.knowledge.jina_reranker import JinaReranker
from app.services.ingestion.metadata_enricher import MetadataEnricher
from app.core.observability.forensic import ForensicRecorder
from app.domain.knowledge_schemas import RAGSearchResult, RetrievalIntent, AgentRole, TaskType
from app.domain.interfaces.retrieval_interface import IRetrievalRepository
from app.services.knowledge.retrieval_strategies import DirectRetrievalStrategy, IterativeRetrievalStrategy, IRetrievalStrategy
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine
from app.services.retrieval.engine import UnifiedRetrievalEngine

logger = structlog.get_logger(__name__)

class RetrievalBroker:
    """
    Central orchestrator for knowledge retrieval.
    Decouples DSPy tools from business logic and infrastructure.
    """
    
    def __init__(self, repository: IRetrievalRepository):
        self.repository = repository
        self.enricher = MetadataEnricher()
        self.reranker = GravityReranker()
        self.jina_reranker = JinaReranker()
        self.direct_strategy = DirectRetrievalStrategy(repository)
        self.iterative_strategy = IterativeRetrievalStrategy(repository)
        self.unified_engine = UnifiedRetrievalEngine()
        self.atomic_engine = AtomicRetrievalEngine()
        self.query_decomposer = QueryDecomposer()

    @staticmethod
    def _engine_mode() -> str:
        mode = str(settings.RETRIEVAL_ENGINE_MODE or "hybrid").strip().lower()
        if mode not in {"unified", "atomic", "hybrid"}:
            return "hybrid"
        return mode

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
                    strategy=("AtomicRetrievalEngine" if mode in {"atomic", "hybrid"} else "UnifiedRetrievalEngine"),
                    filters=filter_conditions,
                    retrieval_engine_mode=mode,
                    planner_used=planner_used,
                    planner_multihop=plan.is_multihop,
                )

                raw_results = []
                if mode in {"atomic", "hybrid"}:
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

                if not raw_results and mode in {"unified", "hybrid"}:
                    hydrated = await self.unified_engine.retrieve_context(
                        query=query,
                        scope_context=filter_conditions,
                        k=k,
                        fetch_k=fetch_k,
                    )
                    raw_results = [item.model_dump() for item in hydrated]

            if not raw_results:
                ForensicRecorder.record_retrieval(query, [], {"scope": scope_context.get("type"), "filters": filter_conditions})
                return []

            ForensicRecorder.record_retrieval(query, raw_results, {"scope": scope_context.get("type"), "filters": filter_conditions})

            # 4. Reranking Phase
            if enable_reranking and raw_results:
                return await self._apply_reranking(query, raw_results, scope_context, k)

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
        _, query_filters = self.enricher.enrich(query, {})
        if query_filters:
            filters["filters"] = query_filters

        # Merge extra filters
        extra = scope_context.get("filters", {})
        if extra:
            filters.update(extra)

        scope_collection = scope_context.get("collection_id")
        if scope_collection and not filters.get("collection_id"):
            filters["collection_id"] = scope_collection
             
        return filters

    async def _apply_reranking(self, query: str, results: List[Dict], scope_context: Dict, k: int) -> List[Dict]:
        try:
            semantic_ranked = results
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

            logger.info(
                "retrieval_pipeline_timing",
                stage="jina_rerank",
                duration_ms=round((time.perf_counter() - jina_started) * 1000, 2),
                enabled=self.jina_reranker.is_enabled(),
                candidates=len(results),
                ranked=len(semantic_ranked),
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
