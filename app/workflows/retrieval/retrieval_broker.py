import structlog
import time
import math
from typing import List, Dict, Any, Optional
from app.infrastructure.settings import settings
from app.domain.schemas.query_plan import PlannedSubQuery, QueryPlan
from app.ai.embeddings import JinaEmbeddingService
from app.ai.rerankers.cohere_reranker import CohereReranker
from app.ai.rerankers.gravity_reranker import GravityReranker
from app.ai.rerankers.jina_reranker import JinaReranker
from app.domain.ingestion.metadata.metadata_enricher import enrich_metadata
from app.infrastructure.observability.forensic import ForensicRecorder
from app.infrastructure.observability.scope_metrics import scope_metrics_store
from app.domain.schemas.knowledge_schemas import RAGSearchResult, RetrievalIntent, AgentRole, TaskType
from app.domain.retrieval.ports import IAuthorityReranker, ISemanticReranker
from app.domain.retrieval.ports import IRetrievalRepository
from app.domain.retrieval.strategies.retrieval_strategies import (
    DirectRetrievalStrategy,
    IterativeRetrievalStrategy,
)
from app.domain.retrieval.fusion import stratify_results, _safe_float
from app.infrastructure.supabase.repositories.atomic_engine import AtomicRetrievalEngine
from app.domain.retrieval.scoping import (
    apply_scope_penalty,
    count_scope_penalized,
    requested_scopes_from_context,
    RetrievalScopeService,
)
from app.domain.retrieval.context_resolution import resolve_retrieval_filters
from app.domain.retrieval.planning import coerce_query_plan

logger = structlog.get_logger(__name__)


class RetrievalBroker:
    """
    Central orchestrator for knowledge retrieval.
    Decouples DSPy tools from business logic and infrastructure.
    """

    def __init__(
        self,
        repository: IRetrievalRepository,
        *,
        authority_reranker: IAuthorityReranker | None = None,
        semantic_reranker: ISemanticReranker | None = None,
        cohere_reranker: ISemanticReranker | None = None,
        atomic_engine: AtomicRetrievalEngine | None = None,
    ):
        self.repository = repository
        self.reranker = authority_reranker or GravityReranker()
        self.jina_reranker = semantic_reranker or JinaReranker()
        self.cohere_reranker = cohere_reranker or CohereReranker()
        self.direct_strategy = DirectRetrievalStrategy(repository)
        self.iterative_strategy = IterativeRetrievalStrategy(repository)
        self.atomic_engine = atomic_engine or AtomicRetrievalEngine()
        self.scope_service = RetrievalScopeService()

    async def close(self) -> None:
        try:
            await self.jina_reranker.close()
            await self.cohere_reranker.close()
        except Exception as exc:
            logger.warning("retrieval_broker_close_failed", error=str(exc))

    @staticmethod
    def _engine_mode() -> str:
        mode = str(settings.RETRIEVAL_ENGINE_MODE or "hybrid").strip().lower()
        if mode == "unified":
            return "hybrid"
        if mode not in {"atomic", "hybrid"}:
            return "hybrid"
        return mode

    @staticmethod
    def _rerank_mode() -> str:
        mode = str(settings.RERANK_MODE or "hybrid").strip().lower()
        if mode not in {"local", "jina", "cohere", "hybrid"}:
            return "hybrid"
        return mode

    def _stamp_results(
        self, 
        rows: List[Dict[str, Any]], 
        tenant_id: str, 
        *, 
        source_layer: Optional[str] = None,
        is_raptor: bool = False
    ) -> List[Dict[str, Any]]:
        """Unified result stamping for ownership and diagnostics."""
        if not rows or not tenant_id:
            return rows or []
            
        self.scope_service.stamp_tenant_context(rows=rows, tenant_id=tenant_id, allowed_source_ids=set())
        
        for row in rows:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata", {})
            if source_layer:
                metadata.setdefault("source_layer", source_layer)
            if is_raptor:
                metadata.setdefault("is_raptor_summary", True)
                
        return rows

    def _apply_scope_penalty(
        self, results: List[Dict[str, Any]], requested_scopes: tuple[str, ...]
    ) -> List[Dict[str, Any]]:
        penalty_factor = float(getattr(settings, "RETRIEVAL_SCOPE_PENALTY_FACTOR", 0.25) or 0.25)
        return apply_scope_penalty(results, requested_scopes, penalty_factor=penalty_factor)

    @staticmethod
    def _count_scope_penalized(results: List[Dict[str, Any]]) -> int:
        return count_scope_penalized(results)

    async def retrieve(
        self,
        query: str,
        scope_context: Dict[str, Any],
        k: int = 20,
        fetch_k: int = 120,
        enable_reranking: bool = True,
        iterative: bool = False,
        return_trace: bool = False,
        **strategy_kwargs,
    ) -> List[Dict[str, Any]] | Dict[str, Any]:
        """Main entry point for retrieval orchestration."""
        if not query:
            return {"items": [], "trace": {"timings_ms": {"total": 0.0}}} if return_trace else []

        # 1. Resolve and normalize filters from context & query
        filter_conditions = resolve_retrieval_filters(query, scope_context)
        trace_payload: Dict[str, Any] = {
            "filters_applied": dict(filter_conditions),
            "engine_mode": self._engine_mode(),
            "planner_used": False,
            "planner_multihop": False,
            "fallback_used": False,
            "score_space": "similarity",
            "timings_ms": {},
        }
        total_started = time.perf_counter()

        try:
            retrieval_started = time.perf_counter()
            
            # 1. Strategy Execution
            if iterative:
                raw_results = await self._execute_iterative_strategy(
                    query, filter_conditions, k, fetch_k, trace_payload, **strategy_kwargs
                )
            else:
                raw_results = await self._execute_atomic_strategy(
                    query, filter_conditions, k, fetch_k, scope_context, trace_payload, **strategy_kwargs
                )

            trace_payload["timings_ms"]["retrieval"] = round(
                (time.perf_counter() - retrieval_started) * 1000, 2
            )
            
            if not raw_results:
                ForensicRecorder.record_retrieval(query, [], {"scope": scope_context.get("type"), "filters": filter_conditions})
                trace_payload["timings_ms"]["total"] = round((time.perf_counter() - total_started) * 1000, 2)
                return {"items": [], "trace": trace_payload} if return_trace else []

            ForensicRecorder.record_retrieval(query, raw_results, {"scope": scope_context.get("type"), "filters": filter_conditions})

            # 2. Reranking Phase
            rerank_started = time.perf_counter()
            if enable_reranking:
                ranked = await self._apply_reranking(query, raw_results, filter_conditions, k, trace_payload=trace_payload)
            else:
                ranked = raw_results[:k]

            trace_payload["timings_ms"]["rerank"] = round((time.perf_counter() - rerank_started) * 1000, 2)
            trace_payload["timings_ms"]["total"] = round((time.perf_counter() - total_started) * 1000, 2)
            
            return {"items": ranked, "trace": trace_payload} if return_trace else ranked

        except Exception as e:
            logger.error("broker_retrieval_failed", error=str(e), query=query)
            raise e

    async def _execute_iterative_strategy(self, query, filters, k, fetch_k, trace, **kwargs):
        engine = JinaEmbeddingService.get_instance()
        vectors = await engine.embed_texts([query], task="retrieval.query")
        if not vectors or not vectors[0]:
            return []
            
        trace["engine_mode"] = "iterative"
        return await self.iterative_strategy.execute(
            query_vector=vectors[0], query_text=query, filter_conditions=filters, k=k, fetch_k=fetch_k, **kwargs
        )

    async def _execute_atomic_strategy(self, query, filters, k, fetch_k, scope_context, trace, **kwargs):
        mode = self._engine_mode()
        skip_planner = bool(scope_context.get("_skip_planner"))
        plan_raw = kwargs.get("retrieval_plan")
        plan = coerce_query_plan(plan_raw)
        
        planner_used = bool(mode in {"atomic", "hybrid"} and not skip_planner and plan and plan.sub_queries)
        plan = plan or QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])
        
        trace.update({
            "engine_mode": mode,
            "planner_used": planner_used,
            "planner_multihop": bool(plan.is_multihop),
            "planner_source": "request" if planner_used else "none",
        })
        if skip_planner: trace["planner_skipped_reason"] = "multi_query_subquery"
        if plan.fallback_reason: trace["planner_fallback_reason"] = str(plan.fallback_reason)

        try:
            if plan.is_multihop:
                raw_results = await self.atomic_engine.retrieve_context_from_plan(
                    query=query, plan=plan, scope_context=filters, k=k, fetch_k=fetch_k, **kwargs
                )
            else:
                raw_results = await self.atomic_engine.retrieve_context(
                    query=query, scope_context=filters, k=k, fetch_k=fetch_k, **kwargs
                )
                
            atomic_trace = getattr(self.atomic_engine, "last_trace", {})
            if isinstance(atomic_trace, dict):
                self._merge_atomic_trace(trace, atomic_trace)
                
        except Exception as exc:
            logger.warning("atomic_engine_failed", error=str(exc), mode=mode)
            if mode == "atomic": raise exc
            raw_results = []

        # Fallbacks
        if not raw_results and mode == "hybrid":
            raw_results = await self._execute_direct_fallback(query, filters, k, fetch_k, trace, **kwargs)
            
        if not raw_results:
            metadata = filters.get("metadata", {})
            clause_id = str(metadata.get("clause_id") or "").strip()
            if clause_id:
                raw_results = await self._execute_literal_clause_fallback(query, filters, clause_id, k, fetch_k, trace, **kwargs)
                
        return raw_results

    def _merge_atomic_trace(self, trace_payload: dict, atomic_trace: dict):
        if not atomic_trace: return
        
        if status := str(atomic_trace.get("rpc_contract_status") or "").strip():
            trace_payload["rpc_contract_status"] = status
            
        if compat := str(atomic_trace.get("rpc_compat_mode") or atomic_trace.get("hybrid_rpc_compat_mode") or "").strip():
            trace_payload["rpc_compat_mode"] = compat
            trace_payload["hybrid_rpc_compat_mode"] = compat
            
        if "hybrid_rpc_used" in atomic_trace:
            trace_payload["hybrid_rpc_used"] = bool(atomic_trace.get("hybrid_rpc_used"))
            
        # Merging warnings
        for key in ["warnings", "warning_codes"]:
            raw = atomic_trace.get(key)
            if isinstance(raw, list):
                prior = trace_payload.get(key, [])
                trace_payload[key] = list(dict.fromkeys([*prior, *[str(i).strip() for i in raw if str(i).strip()]]))

    async def _execute_direct_fallback(self, query, filters, k, fetch_k, trace, **kwargs):
        engine = JinaEmbeddingService.get_instance()
        vectors = await engine.embed_texts([query], task="retrieval.query")
        if vectors and vectors[0]:
            trace["fallback_used"] = True
            return await self.direct_strategy.execute(
                query_vector=vectors[0], query_text=query, filter_conditions=filters, k=k, fetch_k=fetch_k, **kwargs
            )
        return []

    async def _execute_literal_clause_fallback(self, query, filters, clause_id, k, fetch_k, trace, **kwargs):
        fallback_filters = dict(filters)
        fallback_filters.pop("metadata", None)
        nested = fallback_filters.get("filters", {})
        if isinstance(nested, dict):
            fallback_filters["filters"] = {k2: v2 for k2, v2 in nested.items() if str(k2) not in {"clause_id", "clause_refs"}}
            if not fallback_filters["filters"]: fallback_filters.pop("filters", None)

        trace["literal_clause_fallback"] = {"applied": True, "clause_id": clause_id}
        return await self.atomic_engine.retrieve_context(
            query=query, scope_context=fallback_filters, k=k, fetch_k=fetch_k, **kwargs
        )


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

            data = await self.repository.match_summaries(
                vectors[0], tenant_id, k, collection_id=collection_id
            )
            
            data = self._stamp_results(data, tenant_id, is_raptor=True)

            ForensicRecorder.record_retrieval(
                query,
                data,
                {
                    "scope": "raptor_summaries",
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                },
            )
            return data
        except Exception as e:
            logger.error("raptor_retrieval_failed", error=str(e))
            return []

    async def resolve_summaries_to_chunks(
        self, 
        summary_ids: list[str],
        tenant_id: str
    ) -> List[Dict[str, Any]]:
        """Resolve summary nodes to leaf content chunks (RAPTOR Late Grounding)."""
        if not summary_ids:
            return []
            
        try:
            # 1. Trace RAPTOR children down to base chunks
            chunk_ids = await self.repository.resolve_summaries_to_chunk_ids(summary_ids)
            if not chunk_ids:
                return []
                
            # 2. Fetch the actual leaf content chunks via atomic engine
            raw_chunks = await self.atomic_engine._retrieval_repository.fetch_chunks_by_ids(chunk_ids)
            
            # 3. Stamp tenant context
            return self._stamp_results(raw_chunks, tenant_id)
        except Exception as e:
            logger.error("raptor_resolution_failed", error=str(e), summary_ids=summary_ids)
            return []

    async def retrieve_graph_nodes(
        self,
        query: str,
        tenant_id: str,
        graph_options: dict[str, Any],
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieves purely topological graph context directly via AtomicEngine."""
        if not query or not tenant_id:
            return []
            
        try:
            engine = JinaEmbeddingService.get_instance()
            vectors = await engine.embed_texts([query], task="retrieval.query")
            if not vectors or not vectors[0]:
                return []
                
            scope_context = {"tenant_id": tenant_id, "collection_id": collection_id}
            
            # Use atomic engine to resolve the graph hops directly
            data = await self.atomic_engine._graph_hop(
                query_vector=vectors[0],
                scope_context=scope_context,
                fetch_k=k * 2,
                graph_filter_relation_types=graph_options.get("relation_types"),
                graph_filter_node_types=graph_options.get("node_types"),
                graph_max_hops=graph_options.get("max_hops") or 2,
            )
            
            data = self._stamp_results(data, tenant_id, source_layer="graph")
            
            ForensicRecorder.record_retrieval(
                query,
                data,
                {
                    "scope": "graph_nodes",
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                },
            )
            return sorted(data, key=lambda x: float(x.get("score") or 0.0), reverse=True)[:k]
        except Exception as e:
            logger.error("graph_retrieval_failed", error=str(e))
            return []


    async def _apply_reranking(
        self,
        query: str,
        results: List[Dict],
        scope_context: Dict,
        k: int,
        *,
        trace_payload: Dict[str, Any] | None = None,
    ) -> List[Dict]:
        try:
            rerank_mode = self._rerank_mode()
            requested_scopes = requested_scopes_from_context(scope_context)
            rerank_started = time.perf_counter()
            skip_external_rerank = bool(scope_context.get("_skip_external_rerank"))

            working_results = results

            # 1. Local Gravity Reranking — ALWAYS runs.
            # This is the business-rule layer: heading_boost, authority weights,
            # structural intent.  It is NOT optional; it forms the foundation
            # that semantic rerankers refine on top of.
            working_results = await self._execute_gravity_rerank(
                query, results, working_results, scope_context,
                len(working_results),  # Evaluate all initial candidates
                trace_payload
            )

            # 2. Scope Penalty & Metrics
            if requested_scopes:
                penalty_factor = float(getattr(settings, "RETRIEVAL_SCOPE_PENALTY_FACTOR", 0.25) or 0.25)
                working_results = apply_scope_penalty(working_results, requested_scopes, penalty_factor=penalty_factor)
                
            candidate_count = len(working_results)
            penalized_count = count_scope_penalized(working_results)
            
            self._record_rerank_metrics(scope_context, penalized_count, candidate_count, requested_scopes, trace_payload)

            if requested_scopes and settings.SCOPE_STRICT_FILTERING:
                working_results = [i for i in working_results if not bool(i.get("scope_penalized"))] or working_results

            # 3. Stratified Semantic External Reranking (Jina/Cohere)
            if requested_scopes and len(requested_scopes) > 1:
                config_max = int(getattr(settings, "RERANK_MAX_CANDIDATES", 150) or 150)
                # Ensure we evaluate a wide pool to rescue hidden scopes during stratification
                max_c = max(1, min(config_max, len(working_results)))
                working_results = stratify_results(working_results, requested_scopes, max_c)

            if not skip_external_rerank and working_results:
                working_results = await self._execute_external_rerank(query, working_results, rerank_mode, k)

            if isinstance(trace_payload, dict):
                trace_payload["score_space"] = "semantic_relevance" if not skip_external_rerank else "gravity"

            if requested_scopes and len(requested_scopes) > 1:
                return stratify_results(working_results, requested_scopes, k)
            return working_results[:k]

        except Exception as e:
            logger.warning("reranking_fallback", error=str(e))
            if requested_scopes and len(requested_scopes) > 1:
                return stratify_results(results, requested_scopes, k)
            return results[:k]

    async def _execute_external_rerank(self, query, results, mode, k):
        active = None
        if mode == "cohere" and self.cohere_reranker.is_enabled(): active = self.cohere_reranker
        elif mode in {"jina", "hybrid"} and self.jina_reranker.is_enabled(): active = self.jina_reranker
        
        if not active: return results
        
        # Estrategia 3: Enviamos un espectro amplio a Jina/Cohere (pre-filtrado por Gravity)
        # para prevenir inanición semántica y recuperar información oculta.
        config_max = int(getattr(settings, "RERANK_MAX_CANDIDATES", 150) or 150)
        max_c = max(1, min(config_max, len(results)))
        candidates = results[:max_c]
        
        rows = await active.rerank_documents(
            query=query, 
            documents=[str(i.get("content") or "") for i in candidates], 
            top_n=min(k, len(candidates))
        )
        
        if not rows: return results
        
        reordered = []
        for r in rows:
            idx = r.get("index")
            if 0 <= idx < len(candidates):
                source = dict(candidates[idx])
                jina_score = _safe_float(r.get("relevance_score"), default=0.0)
                source["semantic_relevance_score"] = jina_score
                
                # Jina tends to sink structurally-matched chunks because semantically they are loose.
                # Here we respect the heading_boost applied during GravityRerank Phase.
                boost = float(source.get("metadata", {}).get("heading_boost", 1.0))
                if boost > 1.0:
                    # Jina scores max out around 1.0. If the baseline semantic score is tiny (e.g., 0.05),
                    # multiplying it by 3.0 or 5.0 still leaves it below purely semantic matches (e.g., 0.8).
                    # We establish a functional floor so the boost propels it above semantic matches.
                    jina_score = max(jina_score, 0.3) * boost  # Structural intent overrules raw semantic
                    
                if "jina" in active.__class__.__name__.lower():
                    source["jina_relevance_score"] = jina_score
                
                source["combined_semantic_score"] = jina_score
                reordered.append(source)
                
        # Re-sort to respect the structural boosts injected above
        reordered.sort(key=lambda x: x.get("combined_semantic_score", 0.0), reverse=True)
                
        # Fill the rest with non-reranked candidates just in case we need 'k' elements
        return reordered + [c for i, c in enumerate(results) if i not in [r.get("index") for r in rows]]

    async def _execute_gravity_rerank(self, query, original_results, current_results, scope_context, k, trace):
        raw_by_id = {str(item.get("id", "")): item for item in original_results}
        candidates = [
            RAGSearchResult(
                id=str(i.get("id", "")), content=i.get("content", ""), metadata=i.get("metadata", {}),
                similarity=_safe_float(i.get("jina_relevance_score", i.get("similarity", 0.0)), default=0.0),
                score=_safe_float(i.get("jina_relevance_score", i.get("similarity", 0.0)), default=0.0),
                source_layer="knowledge", source_id=i.get("source_id"),
            ) for i in current_results
        ]
        intent = RetrievalIntent(
            query=query, role=AgentRole(scope_context.get("role", "socratic_mentor").lower()), task=TaskType.EXPLANATION
        )
        ranked = self.reranker.rerank(candidates, intent)
        merged = []
        for rc in ranked[:k]:
            src_orig = raw_by_id.get(str(rc.id), {})
            # Keep all original database fields intact
            p = dict(src_orig)
            orig_meta = p.get("metadata", {}) if isinstance(p.get("metadata"), dict) else {}
            # Merge metadata: keep all original database fields and update with Gravity metrics
            merged_meta = dict(orig_meta)
            if rc.metadata:
                merged_meta.update(rc.metadata)
            p["metadata"] = merged_meta
            
            # Ensure top-level ownership fields are set
            for own_key in ("tenant_id", "institution_id"):
                val = merged_meta.get(own_key) or p.get(own_key)
                if val:
                    p[own_key] = val
            p["similarity"] = rc.similarity
            p["score"] = rc.score
            p["score_space"] = "gravity"
            merged.append(p)
            
        if isinstance(trace, dict): trace["score_space"] = "gravity"
        return merged

    def _record_rerank_metrics(self, scope_context, penalized, total, requested, trace):
        tenant_id = str(scope_context.get("tenant_id") or "")
        scope_metrics_store.record_rerank_penalized(tenant_id=tenant_id, penalized_count=penalized, candidate_count=total)
        
        if isinstance(trace, dict):
            ratio = round((penalized / total) if total else 0.0, 4)
            trace.update({
                "scope_penalized_count": int(penalized),
                "scope_candidate_count": int(total),
                "scope_penalized_ratio": float(ratio),
                "requested_scopes": list(requested),
            })
