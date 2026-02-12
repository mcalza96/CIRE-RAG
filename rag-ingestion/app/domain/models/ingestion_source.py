from abc import ABC, abstractmethod
from typing import Optional, BinaryIO

class IngestionSource(ABC):
    """
    Abstract interface for a source file to be ingested.
    This creates a contract that different file wrappers (FastAPI UploadFile, LocalFile) 
    must adhere to, fixing LSP violations.
    """
    
    @abstractmethod
    def get_filename(self) -> str:
         """Returns the name of the file."""
         pass
         
    @abstractmethod
    def get_file_path(self) -> Optional[str]:
         """
         Returns the local file path if available. 
         Some libraries (like PyMuPDF) optimize when given a path.
         Returns None if the file is only available as a stream.
         """
         pass

    @abstractmethod
    async def get_content(self) -> bytes:
        """Returns the full content of the file."""
        pass
