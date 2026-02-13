from fastapi import APIRouter
from app.api.v1.routers.ingestion import router as ingestion_router
# from app.api.v1.routers.cognitive import router as cognitive_router
# from app.api.v1.routers.audit import router as audit_router
from app.api.v1.routers.knowledge import router as knowledge_router
from app.api.v1.routers.retrieval import router as retrieval_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(ingestion_router)
v1_router.include_router(retrieval_router)
# v1_router.include_router(cognitive_router)
# v1_router.include_router(audit_router)
v1_router.include_router(knowledge_router, prefix="/knowledge")
