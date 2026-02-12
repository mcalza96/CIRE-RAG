import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection
from app.core.settings import settings

logger = logging.getLogger(__name__)

class DatabaseManager:
    _pool: AsyncConnectionPool = None

    @classmethod
    async def get_pool(cls) -> AsyncConnectionPool:
        if cls._pool is None:
            # Plan specified DATABASE_URL, supporting SUPABASE_DB_URL as fallback if present
            database_url = settings.DATABASE_URL or settings.SUPABASE_DB_URL
            if not database_url:
                raise ValueError("DATABASE_URL (or SUPABASE_DB_URL) environment variable is not set")
            
            logger.info("Initializing AsyncConnectionPool for LangGraph persistence...")
            
            cls._pool = AsyncConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=20,
                open=False,
                kwargs={
                    "autocommit": True # LangGraph persistence often benefits from autocommit or manual txn management
                }
            )
            await cls._pool.open()
            logger.info("AsyncConnectionPool initialized successfully.")
            
        return cls._pool

    @classmethod
    async def close_pool(cls):
        if cls._pool:
            await cls._pool.close()
            cls._pool = None
            logger.info("AsyncConnectionPool closed.")

    _checkpointer_setup_done = False

    @classmethod
    async def get_checkpointer(cls):
        """
        Returns an AsyncPostgresSaver instance for LangGraph persistence.
        """
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        
        pool = await cls.get_pool()
        saver = AsyncPostgresSaver(pool)
        
        # Phase 8: Hardening - Automatic table setup
        if not cls._checkpointer_setup_done:
            logger.info("Setting up LangGraph checkpointer tables...")
            await saver.setup()
            cls._checkpointer_setup_done = True
            
        return saver

    @classmethod
    @asynccontextmanager
    async def get_connection(cls) -> AsyncGenerator[AsyncConnection, None]:
        """
        Yields an async connection from the pool.
        """
        pool = await cls.get_pool()
        async with pool.connection() as conn:
            yield conn
