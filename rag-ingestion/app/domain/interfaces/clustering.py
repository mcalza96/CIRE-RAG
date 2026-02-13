from typing import Protocol
import numpy as np
from typing import List
from uuid import UUID
from app.domain.raptor_schemas import ClusterResult

class IClusteringService(Protocol):
    """
    Interface for semantic clustering services.
    Allows for different clustering algorithms (GMM, K-Means, etc.) 
    to be swapped without affecting the processor.
    """

    def cluster(
        self, 
        chunk_ids: List[UUID], 
        embeddings: np.ndarray
    ) -> ClusterResult:
        """
        Cluster embeddings and assign chunk IDs to clusters.
        """
        ...
