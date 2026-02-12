from abc import ABC, abstractmethod
from typing import TypeVar, Type, Optional, Any, Union
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)

class IStructuredEngine(ABC):
    """
    Interface for structured generation engines.
    Encapsulates constrained decoding logic.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        schema: Type[T],
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> T:
        """
        Generate a structured response conforming to the given schema.
        """
        pass

    @abstractmethod
    def generate_or_error(
        self,
        prompt: str,
        success_schema: Type[T],
        error_schema: Type[BaseModel],
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> Union[T, BaseModel]:
        """
        Generate a response that can be either success or error.
        """
        pass
