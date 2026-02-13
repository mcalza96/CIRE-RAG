import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class SecurityViolationError(Exception):
    """Raised when a severe security violation (data leak) is detected."""
    pass

class LeakCanary:
    """
    Second-Line of Defense (Application Level).
    Verifies that all retrieved documents belong to the authenticated tenant.
    """
    
    @staticmethod
    def verify_isolation(
        current_tenant_id: str, 
        retrieved_docs: List[Dict[str, Any]]
    ) -> None:
        """
        Verify that all documents match the current tenant_id.
        
        Args:
            current_tenant_id: The authenticated tenant ID.
            retrieved_docs: List of document dicts (must have 'metadata').
            
        Raises:
            SecurityViolationError: If a leak is detected.
        """
        if not current_tenant_id:
            raise SecurityViolationError("LeakCanary: Missing current_tenant_id for verification.")

        for i, doc in enumerate(retrieved_docs):
            metadata = doc.get("metadata", {})
            doc_tenant = metadata.get("tenant_id") or metadata.get("institution_id") or doc.get("institution_id") or doc.get("tenant_id")
            
            # If doc has no tenant_id, is it global?
            if not doc_tenant:
                is_global = metadata.get("is_global", False)
                if not is_global:
                    # STRICT RULE: Non-global docs MUST have tenant_id
                    logger.critical(f"SECURITY ALERT: Document {doc.get('id')} has NO tenant_id and is NOT global.")
                    raise SecurityViolationError("Data Integrity Failure: Document missing ownership metadata.")
                continue

            # Check Match
            if doc_tenant != current_tenant_id:
                logger.critical(
                    f"DATA LEAK DETECTED! "
                    f"User Tenant: {current_tenant_id} | "
                    f"Doc Tenant: {doc_tenant} | "
                    f"Doc ID: {doc.get('id')}"
                )
                raise SecurityViolationError(f"Cross-Tenant Data Leak Detected. Incident reported.")
        
        logger.info(f"[LeakCanary] Verified {len(retrieved_docs)} docs for tenant {current_tenant_id}. No leaks.")
