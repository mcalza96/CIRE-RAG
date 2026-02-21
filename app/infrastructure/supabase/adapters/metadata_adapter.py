from typing import Dict, Any, Optional
from app.domain.schemas.ingestion_schemas import IngestionMetadata
from app.domain.types.authority import AuthorityLevel
from app.domain.services.authority_classifier import AuthorityClassifier
from app.infrastructure.settings import settings

class SupabaseMetadataAdapter:
    """
    Adapter to convert Supabase/Postgres record formats into valid Domain Objects (IngestionMetadata).
    Handles sanitization of database-specific quirks (like nil UUIDs).
    """

    def map_to_domain(
        self,
        record: Dict[str, Any], 
        metadata: Dict[str, Any], 
        source_id: str, 
        filename: str, 
        is_global: bool, 
        institution_id: Optional[str]
    ) -> IngestionMetadata:
        """
        Maps raw input data to a clean IngestionMetadata object.
        """
        def sanitize(v):
            # Domain-specific nil UUID handling: 
            # uuid.Nil (0000...) is often sent by some clients as "null" ref.
            if v == '00000000-0000-0000-0000-000000000000': return None
            if isinstance(v, list): return [x for x in v if x != '00000000-0000-0000-0000-000000000000']
            return v

        # Infer authority level from storage path and document metadata
        storage_path = record.get('storage_path', '')
        doc_type = metadata.get('doc_type') or metadata.get('docType')
        enforcement_level = metadata.get('enforcement_level') or metadata.get('enforcementLevel')

        # If enforcement_level is 'hard_constraint', elevate to CONSTITUTION regardless of path
        if enforcement_level == 'hard_constraint':
            authority_level = AuthorityLevel.CONSTITUTION
        else:
            authority_level = AuthorityClassifier.classify(
                storage_path=storage_path,
                doc_type=doc_type,
                filename=filename,
                mode=settings.AUTHORITY_CLASSIFIER_MODE,
            )

        mapping_data = {
            "title": metadata.get("title") or filename,
            "type_id": metadata.get("type_id") or metadata.get("typeId"),
            "context_id": metadata.get("context_id") or metadata.get("contextId"),
            "subject_id": metadata.get("subject_id") or metadata.get("subjectId"),
            "level_ids": metadata.get("level_ids") or metadata.get("levelIds") or [],
            "doc_type": doc_type,
            "source_id": source_id,
            "institution_id": institution_id,
            "is_global": is_global,
            "authority_level": authority_level,
            "enforcement_level": enforcement_level,
            "metadata": metadata.get("metadata") or {}
        }
        
        # Clean and construct
        cleaned_data = {k: sanitize(v) for k, v in mapping_data.items() if sanitize(v) is not None}
        return IngestionMetadata(**cleaned_data)
