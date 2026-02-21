"""
Refactored Retrieval Tool (Phase 4)
===================================

Implements strict "Metadata Partitioning" for RAG.
Enforces tenant isolation at the query level using the secure RPC.
"""

from typing import List, Optional, Dict, Any
import dspy

from app.workflows.retrieval.retrieval_broker import RetrievalBroker
from app.domain.retrieval.ports import IRetrievalRepository
from app.domain.retrieval.config import retrieval_settings

class TenantContextMissingError(ValueError):
    """Raised when institutional retrieval is attempted without a tenant_id."""
    pass

class RetrievalTools(dspy.Retrieve):
    """
    DSPy-compatible Retrieval Module.
    Refactored: Now delegates all logic to RetrievalBroker.
    """
    
    def __init__(self, repository: IRetrievalRepository, broker: Optional[RetrievalBroker] = None, k: int = retrieval_settings.TOP_K):
        super().__init__(k=k)
        self.repository = repository
        self.broker = broker or RetrievalBroker(repository)
    
    async def forward(self, query: str, k: Optional[int] = None, **kwargs) -> List[str]:
        """
        DSPy forward pass. Returns STRINGS for the LLM.
        """
        scope = kwargs.get("scope_context") or kwargs.get("context")
        if not scope:
            raise TenantContextMissingError("Secure Retrieval requires a 'scope_context'.")
            
        results = await self.retrieve(query=query, scope_context=scope, k=k or self.k)
        return [r["content"] for r in results]

    async def retrieve(
        self,
        query: str,
        scope_context: Dict[str, Any],
        k: int = retrieval_settings.TOP_K,
        fetch_k: int = 50,
        enable_reranking: bool = True,
        return_trace: bool = False,
        graph_filter_relation_types: Optional[list[str]] = None,
        graph_filter_node_types: Optional[list[str]] = None,
        graph_max_hops: Optional[int] = None,
    ) -> Any:
        """
        Delegates to RetrievalBroker.
        """
        return await self.broker.retrieve(
            query=query,
            scope_context=scope_context,
            k=k,
            fetch_k=fetch_k,
            enable_reranking=enable_reranking,
            return_trace=return_trace,
            graph_filter_relation_types=graph_filter_relation_types,
            graph_filter_node_types=graph_filter_node_types,
            graph_max_hops=graph_max_hops,
        )

    async def retrieve_summaries(
        self,
        query: str,
        tenant_id: str,
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Delegates to RetrievalBroker.
        """
        return await self.broker.retrieve_summaries(query, tenant_id, k, collection_id=collection_id)

    async def retrieve_iterative(
        self,
        query: str,
        scope_context: Dict[str, Any],
        target_recall_k: int = 120,
        page_size: int = 40,
        novelty_threshold: float = 0.10,
        final_k: int = 20,
        enable_reranking: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Delegates iterative retrieval to RetrievalBroker.
        """
        return await self.broker.retrieve(
            query=query,
            scope_context=scope_context,
            k=final_k,
            fetch_k=target_recall_k,
            enable_reranking=enable_reranking,
            iterative=True,
            target_recall_k=target_recall_k,
            page_size=page_size,
            novelty_threshold=novelty_threshold
        )

    async def retrieve_graph_nodes(
        self,
        query: str,
        tenant_id: str,
        graph_options: dict[str, Any],
        k: int = 5,
        collection_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Delegates graph context retrieval to RetrievalBroker.
        """
        return await self.broker.retrieve_graph_nodes(
            query=query,
            tenant_id=tenant_id,
            graph_options=graph_options,
            k=k,
            collection_id=collection_id,
        )
