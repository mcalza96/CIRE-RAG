"""Structured synthesis job API router."""
import structlog
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional

from app.infrastructure.supabase.client import get_async_supabase_client
from enum import Enum
import uuid

# Removed direct graph import to avoid blocking headers
# from app.workflows.curriculum.graph import curriculum_graph 
# from app.workflows.curriculum.state import CurriculumState

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["structured-synthesis"])
JOB_TYPE = "structured_synthesis_generation"

class GenerateCurriculumRequest(BaseModel):
    topic: str
    course_level: str
    source_document_id: str
    tenant_id: str = Field(..., description="Tenant ID for RLS isolation")

class ConceptResponse(BaseModel):
    title: str
    summary: str
    rationale: str
    linked_chunk_ids: List[str]

class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[dict] = None
    error_message: Optional[str] = None

@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_status(job_id: str):
    """
    Check the status of a background job.
    """
    try:
        supabase = await get_async_supabase_client()
        res = await supabase.table("job_queue").select("*").eq("id", job_id).single().execute()
        
        if not res.data:
             raise HTTPException(status_code=404, detail="Job not found")
             
        job = res.data
        return JobResponse(
            job_id=job["id"],
            status=JobStatus(job["status"]),
            result=job.get("result"),
            error_message=job.get("error_message")
        )
    except Exception as e:
        logger.error(f"Failed to fetch job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate", response_model=JobResponse, status_code=202)
async def generate_curriculum(request: GenerateCurriculumRequest):
    """
    Enqueues a structured synthesis generation job.
    Returns 202 Accepted with the job_id.
    """
    logger.info(f"Enqueueing structured synthesis job for topic '{request.topic}'")
    
    try:
        supabase = await get_async_supabase_client()
        
        # Payload to allow worker to reconstruct state
        payload = request.model_dump()
        
        # Insert into job_queue
        # Note: In a real auth scenario, we might want to grab user_id from context/token
        # For now, we rely on the worker having checks or the RLS handling insertion if authed.
        # Since this is a service-to-service or backend API, we assume it can write.
        
        job_data = {
            "job_type": JOB_TYPE,
            "status": "pending",
            "payload": payload,
            "tenant_id": request.tenant_id
            # "user_id": ... (if available)
        }
        
        res = await supabase.table("job_queue").insert(job_data).execute()
        
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to enqueue job")
            
        job = res.data[0]
        
        return JobResponse(
            job_id=job["id"],
            status=JobStatus.PENDING
        )
        
    except Exception as e:
        logger.error(f"Failed to enqueue job: {e}")
        raise HTTPException(status_code=500, detail=str(e))
