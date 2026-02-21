from abc import ABC, abstractmethod

from app.domain.schemas.raptor_schemas import SummaryNode


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

    @abstractmethod
    async def save_summary_nodes(self, nodes: list[SummaryNode]) -> None:
        """Persist summary nodes in batch."""
        pass
