import logging
import asyncio
from typing import Dict, Any, Callable, Coroutine
from uuid import uuid4
from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)

# Simple in-memory job store for MVP status checking (optional)
# In prod, use Redis or DB.
JOB_STORE: Dict[str, str] = {}

class BackgroundTaskManager:
    """
    Manages fire-and-forget background jobs.
    Wraps FastAPI's BackgroundTasks.
    """
    
    @staticmethod
    def add_task(
        background_tasks: BackgroundTasks, 
        func: Callable[..., Coroutine[Any, Any, None]], 
        *args, 
        **kwargs
    ) -> str:
        """
        Enqueues a background task and returns a Job ID.
        """
        job_id = str(uuid4())
        
        async def wrapper(*w_args, **w_kwargs):
            try:
                JOB_STORE[job_id] = "RUNNING"
                logger.info(f"Starting Background Job {job_id}")
                await func(*w_args, **w_kwargs)
                JOB_STORE[job_id] = "COMPLETED"
                logger.info(f"Completed Background Job {job_id}")
            except Exception as e:
                JOB_STORE[job_id] = "FAILED"
                logger.error(f"Job {job_id} Failed: {e}")
                # Ideally write failure to DB here too
                
        background_tasks.add_task(wrapper, *args, **kwargs)
        return job_id

    @staticmethod
    def get_status(job_id: str) -> str:
        return JOB_STORE.get(job_id, "UNKNOWN")
