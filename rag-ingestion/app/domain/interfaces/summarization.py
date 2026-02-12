from abc import ABC, abstractmethod
from typing import List, Tuple

class ISummarizationService(ABC):
    """
    Interface for document summarization services.
    Encapsulates LLM-based summarization logic.
    """

    @abstractmethod
    def summarize(self, texts: List[str]) -> Tuple[str, str]:
        """
        Summarize a list of texts into a single cohesive summary.
        Returns a Tuple of (title, summary).
        """
        pass
