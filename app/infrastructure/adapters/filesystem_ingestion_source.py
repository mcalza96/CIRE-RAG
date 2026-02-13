from typing import Optional
from app.domain.models.ingestion_source import IngestionSource

class FileSystemIngestionSource(IngestionSource):
    """
    Infrastructure implementation of IngestionSource for local files.
    """
    def __init__(self, file_path: str, filename: str, content_type: str = "application/pdf"):
        self.file_path = file_path
        self.filename = filename
        self.content_type = content_type
        # File is opened on demand or kept open if needed
        self._file = open(file_path, "rb")

    def get_filename(self) -> str:
        return self.filename

    def get_file_path(self) -> Optional[str]:
        return self.file_path

    async def get_content(self) -> bytes:
        self._file.seek(0)
        return self._file.read()

    @property
    def file(self):
        return self._file

    async def read(self, size: int = -1) -> bytes:
        return self._file.read(size)

    async def seek(self, offset: int) -> None:
        self._file.seek(offset)

    async def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()
