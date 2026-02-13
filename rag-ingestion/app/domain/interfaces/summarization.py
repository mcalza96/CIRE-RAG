from typing import List, Tuple, Protocol

class ISummarizationService(Protocol):
    """
    Interface for document summarization services.
    Encapsulates LLM-based summarization logic.
    """

    def summarize(self, texts: List[str]) -> Tuple[str, str]:
        """
        Summarize a list of texts into a single cohesive summary.
        Returns a Tuple of (title, summary).
        """
        ...
