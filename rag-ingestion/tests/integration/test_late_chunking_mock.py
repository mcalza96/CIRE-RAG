import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ingestion.chunking_service import ChunkingService
from app.services.ingestion.pdf_parser import PdfParserService


class TestChunkingServiceLateChunking(unittest.IsolatedAsyncioTestCase):
    async def test_uses_late_chunking_by_default(self):
        parser = MagicMock(spec=PdfParserService)
        service = ChunkingService(parser)

        text = "## Capitulo 1\nRegla principal.\n\n## Capitulo 2\nExcepcion operativa."

        mock_embedding_service = MagicMock()
        mock_embedding_service.chunk_and_encode = AsyncMock(
            return_value=[
                {
                    "content": "Regla principal.",
                    "embedding": [0.1, 0.2, 0.3],
                    "char_start": 14,
                    "char_end": 30,
                },
                {
                    "content": "Excepcion operativa.",
                    "embedding": [0.4, 0.5, 0.6],
                    "char_start": 46,
                    "char_end": 66,
                },
            ]
        )
        mock_embedding_service.embed_texts = AsyncMock(return_value=[])

        with patch(
            "app.services.ingestion.chunking_service.JinaEmbeddingService.get_instance",
            return_value=mock_embedding_service,
        ):
            chunks = await service.chunk_document_with_late_chunking(
                full_text=text,
                embedding_mode="LOCAL",
                max_chars=200,
            )

        self.assertEqual(len(chunks), 2)
        mock_embedding_service.chunk_and_encode.assert_awaited_once()
        mock_embedding_service.embed_texts.assert_not_awaited()
        self.assertTrue(chunks[0].get("heading_path"))

    async def test_falls_back_to_contextual_chunking(self):
        parser = MagicMock(spec=PdfParserService)
        service = ChunkingService(parser)

        text = "## Seccion A\nContenido A.\n\n## Seccion B\nContenido B."

        mock_embedding_service = MagicMock()
        mock_embedding_service.chunk_and_encode = AsyncMock(side_effect=RuntimeError("late chunking down"))
        mock_embedding_service.embed_texts = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])

        with patch(
            "app.services.ingestion.chunking_service.JinaEmbeddingService.get_instance",
            return_value=mock_embedding_service,
        ):
            chunks = await service.chunk_document_with_late_chunking(
                full_text=text,
                embedding_mode="LOCAL",
                max_chars=200,
            )

        self.assertEqual(len(chunks), 2)
        mock_embedding_service.embed_texts.assert_awaited_once()
        self.assertTrue(chunks[0]["content"].startswith("[PARENT_CONTEXT]"))


if __name__ == "__main__":
    unittest.main()
