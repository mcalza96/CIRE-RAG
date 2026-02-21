from typing import List, Dict, Any, Optional
from app.domain.interfaces.retrieval_interface import IRetrievalRepository
from app.infrastructure.supabase.client import get_async_supabase_client
import structlog

from app.infrastructure.observability.context_vars import get_tenant_id
from app.domain.retrieval_config import retrieval_settings

logger = structlog.get_logger(__name__)

class SupabaseRetrievalRepository(IRetrievalRepository):
    """
    Supabase Implementation of the Retrieval Repository.
    Encapsulates RPC calls and database-specific formatting.
    """
    
    @staticmethod
    def _resolve_required_tenant(filter_conditions: Dict[str, Any]) -> str:
        from_filter = str((filter_conditions or {}).get("tenant_id") or "").strip()
        from_ctx = str(get_tenant_id() or "").strip()
        tenant_id = from_filter or from_ctx
        if not tenant_id:
            raise ValueError("TENANT_CONTEXT_REQUIRED")

        if from_filter and from_ctx and from_filter != from_ctx:
            raise ValueError("TENANT_MISMATCH")

        return tenant_id

    async def match_knowledge(
        self, 
        vector: List[float], 
        filter_conditions: Dict[str, Any], 
        limit: int,
        query_text: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        tenant_id = self._resolve_required_tenant(filter_conditions)
        scoped_filters = dict(filter_conditions or {})
        scoped_filters["tenant_id"] = tenant_id
        
        rpc_params = {
            "query_embedding": vector,
            "filter_conditions": scoped_filters,
            "match_count": limit,
            "match_threshold": retrieval_settings.MATCH_THRESHOLD_DEFAULT,
            "query_text": query_text
        }
        
        try:
            res = await client.rpc("match_knowledge_secure", rpc_params).execute()
            return res.data or []
        except Exception as e:
            logger.error("supabase_rpc_failed", rpc="match_knowledge_secure", error=str(e))
            raise e

    async def match_knowledge_paginated(
        self,
        vector: List[float],
        filter_conditions: Dict[str, Any],
        limit: int,
        query_text: Optional[str] = None,
        cursor_score: Optional[float] = None,
        cursor_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        tenant_id = self._resolve_required_tenant(filter_conditions)
        scoped_filters = dict(filter_conditions or {})
        scoped_filters["tenant_id"] = tenant_id

        rpc_params = {
            "query_embedding": vector,
            "filter_conditions": scoped_filters,
            "match_count": limit,
            "match_threshold": retrieval_settings.MATCH_THRESHOLD_DEFAULT,
            "query_text": query_text,
            "cursor_score": cursor_score,
            "cursor_id": cursor_id,
        }

        try:
            res = await client.rpc("match_knowledge_paginated", rpc_params).execute()
            return res.data or []
        except Exception as e:
            logger.error("supabase_rpc_failed", rpc="match_knowledge_paginated", error=str(e))
            raise e

    async def match_summaries(
        self, 
        vector: List[float], 
        tenant_id: str, 
        limit: int,
        collection_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        tenant_ctx = str(get_tenant_id() or "").strip()
        tenant_req = str(tenant_id or "").strip()
        if not tenant_req:
            raise ValueError("TENANT_CONTEXT_REQUIRED")
        if tenant_ctx and tenant_ctx != tenant_req:
            raise ValueError("TENANT_MISMATCH")
        
        rpc_params = {
            "query_embedding": vector,
            "match_threshold": 0.4,
            "match_count": limit,
            "p_tenant_id": tenant_req
        }
        if collection_id:
            rpc_params["p_collection_id"] = collection_id
        
        try:
            res = await client.rpc("match_summaries", rpc_params).execute()
            return res.data or []
        except Exception as e:
            logger.error("supabase_rpc_failed", rpc="match_summaries", error=str(e))
            return [] # Fail open for summaries as per requirement
