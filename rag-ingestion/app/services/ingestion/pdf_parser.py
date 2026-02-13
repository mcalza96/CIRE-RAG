from typing import List, Dict, Optional, Any
import structlog
import re

logger = structlog.get_logger(__name__)


class PdfParserService:
    """
    Service for extracting text and metadata from PDF files.
    
    Supports two extraction modes:
    1. extract_markdown_with_structure() — Uses pymupdf4llm for markdown output 
       preserving tables, headings, and document structure. PREFERRED.
    2. extract_text_with_page_map() — Legacy plain text extraction. FALLBACK.
    """

    def extract_markdown_with_structure(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Extracts structured markdown from PDF preserving tables, headings, and layout.
        Uses pymupdf4llm for high-fidelity markdown conversion with image support.
        
        :param file_path: Path to the local PDF file.
        :return: Dict with 'full_text' (markdown), 'page_chunks' (per-page), 
                 'page_map', 'visual_tasks', and 'total_pages'. None on failure.
        """
        try:
            import pymupdf4llm
            import os
        except ImportError:
            logger.warning("pymupdf4llm_not_installed_fallback_to_plain_text")
            return self.extract_text_with_page_map(file_path)

        try:
            # Create a directory for images relative to the PDF
            base_name = os.path.basename(file_path)
            image_dir = os.path.join(os.path.dirname(file_path), f"{base_name}_images")
            os.makedirs(image_dir, exist_ok=True)

            # pymupdf4llm.to_markdown with page_chunks=True returns a list of dicts:
            # [{"metadata": {"page": 0, ...}, "text": "markdown content"}, ...]
            page_results = pymupdf4llm.to_markdown(
                file_path,
                page_chunks=True,
                show_progress=False,
                write_images=True,
                image_path=image_dir,
                image_format="png",
            )

            if not page_results:
                logger.warning("empty_pdf_no_pages", file_path=file_path)
                return None

            full_text = ""
            page_map = []
            page_chunks = []
            visual_tasks = []
            current_char = 0

            # Regex to find images in markdown: ![](path/to/image.png)
            img_regex = re.compile(r"!\[\]\((.*?)\)")

            for item in page_results:
                page_data = item if isinstance(item, dict) else {}
                page_num = int(page_data.get("metadata", {}).get("page", 0)) + 1  # 1-indexed
                page_text = str(page_data.get("text", "")).strip()

                if not page_text:
                    continue

                # Find images on this page
                matches = img_regex.findall(page_text)
                for img_rel_path in matches:
                    # Resolve absolute path for the task
                    abs_img_path = os.path.join(image_dir, os.path.basename(img_rel_path))
                    if os.path.exists(abs_img_path):
                        visual_tasks.append({
                            "page": page_num,
                            "image_path": abs_img_path,
                            "content_type": "table" if "table" in page_text.lower() else "figure",
                            "metadata": {"source_page": page_num}
                        })

                start = current_char
                end = start + len(page_text)

                page_map.append({
                    "page": page_num,
                    "start": start,
                    "end": end,
                })

                page_chunks.append({
                    "page": page_num,
                    "markdown": page_text,
                    "char_start": start,
                    "char_end": end,
                })

                full_text += page_text + "\n\n"
                current_char = len(full_text)

            total_pages = len(page_results)

            logger.info(
                "markdown_extraction_complete",
                file=file_path,
                chars=len(full_text),
                pages=total_pages,
                chunks=len(page_chunks),
                visual_tasks=len(visual_tasks),
            )

            return {
                "full_text": full_text,
                "page_map": page_map,
                "page_chunks": page_chunks,
                "visual_tasks": visual_tasks,
                "total_pages": total_pages,
            }

        except Exception as e:
            logger.error("markdown_extraction_failed", file_path=file_path, error=str(e))
            # Fallback to legacy extraction
            logger.info("falling_back_to_plain_text_extraction")
            return self.extract_text_with_page_map(file_path)

    def extract_text_with_page_map(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Legacy: Extracts full text and builds a map of character offsets to page numbers.
        :param file_path: Path to the local PDF file.
        :return: Dict with 'full_text' and 'page_map' or None if extraction fails.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            logger.error("pymupdf_not_installed", error=str(e))
            return None

        try:
            doc = fitz.open(file_path)
        except Exception as e:
            logger.error("pdf_open_failed", file_path=file_path, error=str(e))
            return None

        full_text = ""
        current_char = 0
        page_map = []
        
        try:
            for page_num in range(doc.page_count):
                page = doc.load_page(page_num)
                # Clean NULL characters and extract text
                page_text = page.get_text().replace("\x00", "")
                if not page_text.strip():
                    continue
                
                # Heuristic: ensure basic paragraph spacing if missing
                if "\n\n" not in page_text:
                    page_text = page_text.replace("\n", "\n\n")

                start = current_char
                end = start + len(page_text)
                page_map.append({
                    "page": page_num + 1, 
                    "start": start, 
                    "end": end
                })
                
                full_text += page_text + "\n\n" # Safe separation between pages
                current_char = len(full_text)
                
            return {
                "full_text": full_text,
                "page_map": page_map,
                "total_pages": len(doc)
            }
        except Exception as e:
            logger.error("pdf_extraction_failed", file_path=file_path, error=str(e))
            return None
        finally:
            doc.close()

    def get_page_number(self, char_idx: int, page_map: List[Dict]) -> int:
        """Finds the page number for a given character index using the provided map."""
        for p in page_map:
            if char_idx >= p['start'] and char_idx < p['end']:
                return p['page']
        return page_map[-1]['page'] if page_map else 1
