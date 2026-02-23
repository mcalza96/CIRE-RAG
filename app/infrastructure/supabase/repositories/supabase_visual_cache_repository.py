from typing import List, Dict, Any
import structlog
from app.domain.ingestion.ports import IVisualCacheRepository
from app.infrastructure.supabase.client import get_async_supabase_client
from app.infrastructure.settings import settings

logger = structlog.get_logger(__name__)

class SupabaseVisualCacheRepository(IVisualCacheRepository):
    """
    Implementation of IVisualCacheRepository using Supabase.
    """
    def __init__(self):
        self._supabase = None

    async def get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def get_cached_extractions(
        self,
        hashes: List[str],
        content_types: List[str],
        provider: str,
        model_name: str,
        prompt_version: str,
        schema_version: str,
    ) -> List[Dict[str, Any]]:
        if not hashes:
            return []
            
        client = await self.get_client()
        try:
            query = (
                client.table("cache_visual_extractions")
                .select("image_hash,content_type,prompt_version,schema_version,result_data,created_at")
                .eq("provider", provider)
                .eq("model_version", model_name)
                .in_("image_hash", hashes)
            )

            if bool(settings.VISUAL_CACHE_KEY_V2_ENABLED):
                query = (
                    query.eq("prompt_version", prompt_version)
                    .eq("schema_version", schema_version)
                    .in_("content_type", content_types or ["table"])
                )

            response = await query.execute()
            return response.data or []
        except Exception as exc:
            logger.warning("visual_cache_query_failed", error=str(exc))
            return []
