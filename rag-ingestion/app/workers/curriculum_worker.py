import asyncio
import os
import structlog
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from app.infrastructure.supabase.client import get_async_supabase_client
from app.workflows.curriculum.graph import curriculum_graph
from app.workflows.curriculum.state import CurriculumState

logger = structlog.get_logger(__name__)

JOB_TYPES = ("structured_synthesis_generation", "curriculum_generation")
POLL_INTERVAL = 2  # Seconds

async def process_job(job):
    job_id = job["id"]
    payload = job["payload"]
    tenant_id = job["tenant_id"]
    
    logger.info(f"Processing job {job_id}", topic=payload.get("topic"))
    
    supabase = await get_async_supabase_client()
    
    try:
        # Reconstruct State
        # Note: We assumed payload matches Request, which is mostly State compatible fields
        # But CurriculumState needs some specific fields.
        initial_state = CurriculumState(
            topic=payload["topic"],
            course_level=payload["course_level"],
            source_document_id=payload["source_document_id"],
            tenant_id=tenant_id, # Use the one from the job row for security
            retrieved_candidates=[],
            selected_concepts=[],
            error=None
        )
        
        # Run Graph
        result = await curriculum_graph.invoke(initial_state)
        
        if result.get("error"):
            raise Exception(result["error"])
        
        # Success
        concepts = result.get("selected_concepts", [])
        
        # Construct final result
        final_result = {
            "concepts": concepts,
            "source_document_id": payload["source_document_id"]
        }
        
        # Update Job
        await supabase.table("job_queue").update({
            "status": "completed",
            "result": final_result,
            "updated_at": "now()"
        }).eq("id", job_id).execute()
        
        logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        
        # Update Job as Failed
        await supabase.table("job_queue").update({
            "status": "failed",
            "error_message": str(e),
            "updated_at": "now()"
        }).eq("id", job_id).execute()

async def worker_loop():
    logger.info("Starting structured synthesis worker...")
    supabase = await get_async_supabase_client()
    
    while True:
        try:
            processed = False
            for job_type in JOB_TYPES:
                res = await supabase.rpc("fetch_next_job", {"p_job_type": job_type}).execute()
                jobs = res.data
                if jobs and len(jobs) > 0:
                    job = jobs[0]
                    await process_job(job)
                    processed = True
                    break
            if not processed:
                await asyncio.sleep(POLL_INTERVAL)
                
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("Worker stopped")
