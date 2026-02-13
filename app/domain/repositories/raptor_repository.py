from abc import ABC, abstractmethod
from app.domain.raptor_schemas import SummaryNode

class IRaptorRepository(ABC):
    """
    Interface for RAPTOR tree persistence.
    Decouples the processor from specific database implementations.
    """

    @abstractmethod
    async def save_summary_node(self, node: SummaryNode) -> None:
        """
        Persist a summary node to the storage engine.
        """
        pass
