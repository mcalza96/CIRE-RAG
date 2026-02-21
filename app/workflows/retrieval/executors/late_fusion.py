import asyncio
import structlog
from typing import Any, List, Dict, Optional, Tuple
from app.domain.schemas.knowledge_schemas import (
    RetrievalItem, 
    HybridRetrievalRequest, 
    ComprehensiveRetrievalRequest,
    to_retrieval_items
)
from app.domain.retrieval.fusion import fuse_late_results, apply_retrieval_policy_to_items
from app.domain.retrieval.tracing import build_comprehensive_trace

logger = structlog.get_logger(__name__)

class LateFusionExecutor:
    def __init__(self, retrieval_tools: Any, hybrid_runner: Any):
        self.retrieval_tools = retrieval_tools
        self.hybrid_runner = hybrid_runner

    async def execute(self, request: ComprehensiveRetrievalRequest, query: Optional[str] = None) -> Tuple[List[RetrievalItem], Dict[str, Any], List[str]]:
        trace_warnings: List[str] = []
        
        # 1. Prepare sub-requests
        expanded_query = query or request.query
        chunks_req = HybridRetrievalRequest(
            query=expanded_query,
            tenant_id=request.tenant_id,
            collection_id=request.collection_id,
            k=request.k,
            fetch_k=request.fetch_k,
            filters=request.filters,
            rerank=request.rerank,
        )
        
        graph_payload = request.graph.model_dump() if request.graph else {}

        # 2. Concurrent execution
        results = await asyncio.gather(
            self._pipeline_chunks(chunks_req, trace_warnings),
            self._pipeline_graph(
                expanded_query, request.tenant_id, request.collection_id,
                graph_payload, request.k, trace_warnings
            ),
            self._pipeline_raptor(
                expanded_query, request.tenant_id, request.collection_id,
                request.k, trace_warnings
            ),
            return_exceptions=False
        )

        (chunks_items, chunks_trace), graph_items, raptor_items = results

        # 3. Fusion logic
        merged_items = fuse_late_results(
            chunks=chunks_items,
            graph=graph_items,
            raptor=raptor_items,
            k=request.k
        )

        return merged_items, {
            "chunks_trace": chunks_trace,
            "graph_items": graph_items,
            "raptor_items": raptor_items,
        }, trace_warnings

    async def _pipeline_chunks(self, request: HybridRetrievalRequest, trace_warnings: List[str]) -> Tuple[List[RetrievalItem], Dict[str, Any]]:
        try:
            res = await self.hybrid_runner(request)
            for item in res.items:
                item.metadata["fusion_source"] = "chunks"
            return res.items, res.trace.model_dump() if res.trace else {}
        except Exception as e:
            trace_warnings.append(f"chunks_pipeline_failed:{str(e)[:160]}")
            return [], {}

    async def _pipeline_graph(self, query: str, tenant_id: str, collection_id: Optional[str], graph_options: Dict[str, Any], k: int, trace_warnings: List[str]) -> List[RetrievalItem]:
        try:
            raw_nodes = await self.retrieval_tools.retrieve_graph_nodes(
                query=query, tenant_id=tenant_id, graph_options=graph_options, k=k, collection_id=collection_id
            )
            items = to_retrieval_items(raw_nodes)
            for item in items:
                item.metadata["fusion_source"] = "graph"
            return items
        except Exception as e:
            trace_warnings.append(f"graph_pipeline_failed:{str(e)[:160]}")
            return []

    async def _pipeline_raptor(self, query: str, tenant_id: str, collection_id: Optional[str], k: int, trace_warnings: List[str]) -> List[RetrievalItem]:
        try:
            raw_summaries = await self.retrieval_tools.retrieve_summaries(
                query=query, tenant_id=tenant_id, k=k, collection_id=collection_id
            )
            if not raw_summaries:
                return []
            
            summary_ids = [str(s.get("id")) for s in raw_summaries if s.get("id")]
            raw_chunks = await self.retrieval_tools.broker.resolve_summaries_to_chunks(summary_ids, tenant_id)
            items = to_retrieval_items(raw_chunks)
            for item in items:
                item.metadata["fusion_source"] = "raptor"
                item.metadata["retrieved_via"] = "raptor"
                item.metadata["raptor_reasoning"] = "RAPTOR Cluster Expansion"
            return items
        except Exception as e:
            trace_warnings.append(f"raptor_pipeline_failed:{str(e)[:160]}")
            return []
