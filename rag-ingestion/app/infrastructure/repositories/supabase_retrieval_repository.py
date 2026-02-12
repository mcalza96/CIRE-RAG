from typing import List, Dict, Any, Optional
from app.domain.interfaces.retrieval_interface import IRetrievalRepository
from app.infrastructure.supabase.client import get_async_supabase_client
import structlog

from app.core.retrieval_config import retrieval_settings

logger = structlog.get_logger(__name__)

class SupabaseRetrievalRepository(IRetrievalRepository):
    """
    Supabase Implementation of the Retrieval Repository.
    Encapsulates RPC calls and database-specific formatting.
    """
    
    async def match_knowledge(
        self, 
        vector: List[float], 
        filter_conditions: Dict[str, Any], 
        limit: int,
        query_text: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        client = await get_async_supabase_client()
        
        rpc_params = {
            "query_embedding": vector,
            "filter_conditions": filter_conditions,
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

        rpc_params = {
            "query_embedding": vector,
            "filter_conditions": filter_conditions,
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
        
        rpc_params = {
            "query_embedding": vector,
            "match_threshold": 0.4,
            "match_count": limit,
            "p_tenant_id": tenant_id
        }
        if collection_id:
            rpc_params["p_collection_id"] = collection_id
        
        try:
            res = await client.rpc("match_summaries", rpc_params).execute()
            return res.data or []
        except Exception as e:
            logger.error("supabase_rpc_failed", rpc="match_summaries", error=str(e))
            return [] # Fail open for summaries as per requirement
