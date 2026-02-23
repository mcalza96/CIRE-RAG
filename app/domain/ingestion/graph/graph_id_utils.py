import uuid
from unicodedata import normalize

# UUID v5 Namespace for Deterministic Entity Resolution
NAMESPACE_REGULATORY = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

def generate_deterministic_id(entity_name: str, tenant_id: str) -> uuid.UUID:
    """
    Generate a deterministic UUID v5 based on normalized entity name + tenant.
    """
    # Normalize: lowercase, strip whitespace, NFKD unicode normalization
    normalized = normalize("NFKD", entity_name.lower().strip())
    # Remove diacritics for consistency
    normalized = "".join(c for c in normalized if not c in "áéíóúñ")
    # Create seed combining tenant isolation
    seed = f"{tenant_id}:{normalized}"
    return uuid.uuid5(NAMESPACE_REGULATORY, seed)
