import structlog
from typing import Dict, Any, Optional, Tuple
from app.domain.types.ingestion_status import IngestionStatus

logger = structlog.get_logger(__name__)

class IngestionContextResolver:
    """
    Service to resolve the context of an ingestion record.
    Decouples tenant resolution, visibility, and metadata normalization from the Use Case.
    """
    
    def resolve(self, record: Dict[str, Any]) -> Tuple[Optional[str], bool, str, str, Dict[str, Any]]:
        """
        Resolves the full context for a record.
        Returns: (tenant_id, is_global, filename, storage_path, metadata)
        """
        meta = record.get('metadata', {}) or {}
        
        tenant_id = record.get('institution_id') or meta.get('institution_id') or meta.get('tenant_id')
        
        is_global = self._resolve_is_global(record, meta)
        
        filename = record.get('filename') or meta.get('title')
        storage_path = record.get('storage_path') or meta.get('storage_path')
        
        return tenant_id, is_global, filename, storage_path, meta

    def _resolve_is_global(self, record: Dict[str, Any], meta: Dict[str, Any]) -> bool:
        raw = record.get("is_global")
        if raw is None:
            raw = meta.get("is_global")
        return bool(raw) if raw is not None else False

    def extract_observability_context(self, record: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
        """
        Extracts correlation and business IDs for logging.
        """
        meta = record.get('metadata', {}) or {}
        correlation_id = meta.get('correlation_id', 'unknown-async')
        doc_id = record.get('id')
        course_id = record.get('course_id') or meta.get('course_id')
        
        return doc_id, correlation_id, course_id
