from typing import List, Dict, Any, Optional
from app.domain.retrieval.ports import IRetrievalRepository
from app.infrastructure.supabase.client import get_async_supabase_client
import structlog

from app.infrastructure.observability.context_vars import get_tenant_id
from app.domain.retrieval.config import retrieval_settings

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
            "p_tenant_id": tenant_req,
            "p_collection_id": collection_id
        }
        
        try:
            res = await client.rpc("match_summaries", rpc_params).execute()
            return res.data or []
        except Exception as e:
            logger.error("supabase_rpc_failed", rpc="match_summaries", error=str(e))
            return [] # Fail open for summaries as per requirement

    async def resolve_summaries_to_chunk_ids(
        self, 
        summary_ids: List[str]
    ) -> List[str]:
        """
        Recursively resolves RAPTOR summary IDs to their underlying leaf content_chunks.
        """
        if not summary_ids:
            return []
            
        client = await get_async_supabase_client()
        leaf_chunk_ids: set[str] = set()
        
        # We use a loop to traverse down the levels. RAPTOR depth is typically <= 3.
        # Starting with the initial summary_ids
        current_level_summary_ids = list(set(summary_ids))
        max_depth = 5
        current_depth = 0
        
        while current_level_summary_ids and current_depth < max_depth:
            current_depth += 1
            try:
                # Fetch children_ids for current summaries
                response = (
                    await client.table("regulatory_nodes")
                    .select("id, level, children_ids")
                    .in_("id", current_level_summary_ids)
                    .execute()
                )
                
                rows = response.data or []
                if not rows:
                    break
                    
                next_level_summary_ids_batch: set[str] = set()
                all_children_ids: set[str] = set()
                
                for row in rows:
                    cid_list = row.get("children_ids") or []
                    for cid_raw in cid_list:
                        cid = str(cid_raw).strip()
                        if cid:
                            all_children_ids.add(cid)
                
                if not all_children_ids:
                    break
                    
                # Now we need to know which of these children are ALSO summaries
                # Summaries exist in `regulatory_nodes` with `level > 0`.
                # Leaf chunks (or unresolvable entities) won't be found here or will have level 0.
                children_list = list(all_children_ids)
                
                # Fetch which of these children are summaries
                child_nodes_resp = (
                    await client.table("regulatory_nodes")
                    .select("id, level")
                    .in_("id", children_list)
                    .gt("level", 0)  # Only summaries have level > 0
                    .execute()
                )
                
                child_summary_rows = child_nodes_resp.data or []
                child_summary_ids = {str(r.get("id")) for r in child_summary_rows}
                
                # Those that are summaries go to the next iteration
                next_level_summary_ids_batch.update(child_summary_ids)
                
                # Those that are NOT summaries are assumed to be leaf chunks (content_chunks)
                leaves = all_children_ids - child_summary_ids
                leaf_chunk_ids.update(leaves)
                
                current_level_summary_ids = list(next_level_summary_ids_batch)
                
            except Exception as e:
                logger.error("resolve_summaries_to_chunk_ids_failed", error=str(e), depth=current_depth)
                break
                
        return list(leaf_chunk_ids)
