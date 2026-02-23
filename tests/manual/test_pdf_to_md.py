from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.domain.ingestion.chunking.facade import ChunkingService
from app.infrastructure.document_parsers.pdf_parser import PdfParserService


class _NoopEmbeddingService:
    async def chunk_and_encode(
        self, text: str, mode: str | None = None, provider: str | None = None
    ):
        raise RuntimeError("Not used in this manual debug flow")

    async def embed_texts(
        self,
        texts: list[str],
        task: str = "retrieval.passage",
        mode: str | None = None,
        provider: str | None = None,
    ):
        raise RuntimeError("Not used in this manual debug flow")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug extraction/chunking pipeline for a PDF file"
    )
    parser.add_argument("pdf_path", help="Absolute or relative path to the PDF")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Max chars for heading-based chunk split",
    )
    parser.add_argument(
        "--out-dir",
        default=".debug",
        help="Output directory for debug artifacts",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parser = PdfParserService()
    chunker = ChunkingService(parser, embedding_service=_NoopEmbeddingService())

    print(f"Analyzing PDF: {pdf_path}")
    print("1) Running structured markdown extraction...")
    extracted = parser.extract_markdown_with_structure(str(pdf_path))
    if not extracted:
        raise RuntimeError("PDF extraction failed")

    raw_text = str(extracted.get("full_text") or "")
    total_pages = int(extracted.get("total_pages") or 0)
    base_name = pdf_path.stem

    raw_path = out_dir / f"{base_name}_debug_raw.md"
    raw_path.write_text(raw_text, encoding="utf-8")

    print("2) Applying boilerplate cleaning...")
    cleaned_text = chunker._strip_iso_boilerplate(raw_text)
    cleaned_path = out_dir / f"{base_name}_debug_cleaned.md"
    cleaned_path.write_text(cleaned_text, encoding="utf-8")

    print("3) Splitting headings into logical sections...")
    sections = chunker.split_by_headings(cleaned_text, max_chars=max(500, int(args.max_chars)))
    sections_path = out_dir / f"{base_name}_debug_chunks.json"
    sections_path.write_text(json.dumps(sections, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Done")
    print(f"- Pages: {total_pages}")
    print(f"- Raw chars: {len(raw_text)}")
    print(f"- Clean chars: {len(cleaned_text)}")
    print(f"- Sections: {len(sections)}")
    print(f"- Raw markdown: {raw_path}")
    print(f"- Clean markdown: {cleaned_path}")
    print(f"- Chunk JSON: {sections_path}")

    print("Preview first 5 sections:")
    for idx, section in enumerate(sections[:5], start=1):
        heading = str(section.get("heading_path") or "")
        content = str(section.get("content") or "")
        role = chunker.classify_chunk_role(content).get("chunk_role")
        print(
            f"  [{idx}] role={role} heading={heading!r} chars={len(content)} preview={content[:90].replace(chr(10), ' ')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
