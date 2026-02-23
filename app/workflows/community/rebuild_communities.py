from uuid import UUID
from typing import Dict, Any
import structlog
from app.domain.ingestion.knowledge.clustering_service import ClusteringService
from app.infrastructure.supabase.repositories.community_job_repository import CommunityJobRepository

logger = structlog.get_logger(__name__)

class RebuildCommunitiesUseCase:
    """
    Orchestrates the community rebuild process for a specific tenant.
    This logic is shared between the generic Worker (listening to a queue)
    and the Scheduler (running periodically).
    """
    def __init__(
        self,
        clustering_service: ClusteringService = None,
        repository: CommunityJobRepository = None
    ):
        self.clustering_service = clustering_service or ClusteringService()
        self.repository = repository or CommunityJobRepository()

    async def execute(self, tenant_id: str) -> Dict[str, Any]:
        """
        Executes the community rebuild and logs the result.
        """
        try:
            tenant_uuid = UUID(str(tenant_id))
        except Exception as exc:
            logger.error("invalid_tenant_id_for_community_rebuild", tenant_id=tenant_id, error=str(exc))
            return {"ok": False, "reason": "invalid_tenant_id", "tenant_id": tenant_id}

        logger.info("community_rebuild_start", tenant_id=tenant_id)

        try:
            result = await self.clustering_service.rebuild_communities(tenant_id=tenant_uuid)
            payload = {"ok": True, "tenant_id": tenant_id, **result}

            # Log event for audit traceability
            doc_id = await self.repository.get_latest_document_id(tenant_id)
            if doc_id:
                msg = (
                    f"Community rebuild tenant={tenant_id}: ok=True "
                    f"detected={result.get('communities_detected', 0)} "
                    f"persisted={result.get('communities_persisted', 0)}"
                )
                await self.repository.create_ingestion_event(
                    doc_id, tenant_id, msg, "SUCCESS", payload
                )

            return payload

        except Exception as exc:
            logger.error("community_rebuild_failed", tenant_id=tenant_id, error=str(exc))
            payload = {"ok": False, "tenant_id": tenant_id, "error": str(exc)}

            doc_id = await self.repository.get_latest_document_id(tenant_id)
            if doc_id:
                await self.repository.create_ingestion_event(
                    doc_id, tenant_id, f"Community rebuild failed: {str(exc)}", "WARNING", payload
                )
            raise
