from fastapi import APIRouter
from app.api.v1.routers.chat import router as chat_router
from app.api.v1.routers.documents import router as documents_router
from app.api.v1.routers.ingestion import router as ingestion_router
from app.api.v1.routers.management import router as management_router
from app.api.v1.routers.retrieval import router as retrieval_router
# from app.api.v1.routers.cognitive import router as cognitive_router
# from app.api.v1.routers.audit import router as audit_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(documents_router)
v1_router.include_router(chat_router)
v1_router.include_router(management_router)
v1_router.include_router(retrieval_router)
v1_router.include_router(ingestion_router)
# v1_router.include_router(cognitive_router)
# v1_router.include_router(audit_router)
