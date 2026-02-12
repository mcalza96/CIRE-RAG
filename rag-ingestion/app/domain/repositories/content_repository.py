from abc import ABC, abstractmethod
from typing import List, Dict, Any

class IContentRepository(ABC):
    """
    Interface for persisting content chunks.
    Decouples strategies from Supabase specifics.
    """
    
    @abstractmethod
    async def save_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Persists a batch of content chunks.
        
        Args:
            chunks: A list of dictionaries representing the chunks. 
                    Each dict should match the target schema (content_chunks).
        """
    @abstractmethod
    async def delete_chunks_by_source_id(self, source_id: str) -> None:
        """
        Deletes all chunks associated with a specific source.
        Used to ensure ingestion idempotency (clean slate).
        """
        pass
