from supabase import AsyncClient, create_async_client
from app.infrastructure.settings import settings

_async_supabase_client = None


async def get_async_supabase_client() -> AsyncClient:
    """
    Returns a shared instance of the Async Supabase Client using centralized settings.
    """
    global _async_supabase_client
    if _async_supabase_client is None:
        url = settings.SUPABASE_URL
        key = settings.SUPABASE_SERVICE_KEY

        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY (or SERVICE_ROLE) must be set in settings."
            )
        _async_supabase_client = await create_async_client(url, key)
    return _async_supabase_client


def reset_async_supabase_client():
    """
    Invalidates the global Supabase client, forcing recreation on next get.
    Use this when HTTP/2 connections become corrupted.
    """
    global _async_supabase_client
    _async_supabase_client = None
