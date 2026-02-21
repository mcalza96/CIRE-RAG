import os
from typing import BinaryIO, Optional
from app.domain.ingestion.entities import IngestionSource

class MockUploadFile(IngestionSource):
    """
    A mock class that mimics FastAPI's UploadFile for background processing
    and implements IngestionSource interface.
    """
    def __init__(self, file_path: str, filename: str):
        self.filename = filename
        self.file_path = file_path
        self._file: BinaryIO = open(file_path, "rb")

    @property
    def file(self) -> BinaryIO:
        return self._file

    def get_filename(self) -> str:
        return self.filename

    def get_file_path(self) -> Optional[str]:
        return self.file_path

    async def get_content(self) -> bytes:
        self._file.seek(0)
        return self._file.read()

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    async def seek(self, offset: int) -> None:
        self._file.seek(offset)

    async def close(self) -> None:
        if self._file:
            self._file.close()

    def __del__(self):
        # Ensure file is closed if object is garbage collected
        if hasattr(self, "_file") and self._file and not self._file.closed:
            self._file.close()
