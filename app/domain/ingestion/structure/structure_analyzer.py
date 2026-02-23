"""Fast PDF structural analysis utilities for ingestion routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import fitz


@dataclass(frozen=True)
class StructureSignals:
    """Lightweight per-page structural features used for routing."""

    horizontal_lines: int
    vertical_lines: int
    rect_count: int
    words_count: int
    chars_count: int
    short_token_ratio: float
    distinct_x_bins: int
    char_density: float
    has_visual_keyword: bool
    visual_keyword: str | None
    table_bboxes: list[fitz.Rect]


class PdfStructureAnalyzer:
    """Extract cheap, deterministic structural features from PDF pages."""

    VISUAL_KEYWORDS: tuple[str, ...] = (
        "tabla",
        "table",
        "figura",
        "figure",
        "diagram",
        "diagrama",
        "anexo",
        "annex",
    )

    def extract_signals(self, page: fitz.Page) -> StructureSignals:
        """Compute fast structural features for a single page."""

        horizontal_lines, vertical_lines, rect_count = self._count_vector_primitives(page)
        words = page.get_text("words")
        words_count, chars_count, short_token_ratio, distinct_x_bins, char_density = self._compute_text_features(page, words)
        has_keyword, keyword = self._detect_visual_keyword(page)
        table_bboxes = self._detect_table_regions(page)

        return StructureSignals(
            horizontal_lines=horizontal_lines,
            vertical_lines=vertical_lines,
            rect_count=rect_count,
            words_count=words_count,
            chars_count=chars_count,
            short_token_ratio=short_token_ratio,
            distinct_x_bins=distinct_x_bins,
            char_density=char_density,
            has_visual_keyword=has_keyword,
            visual_keyword=keyword,
            table_bboxes=table_bboxes,
        )

    @staticmethod
    def _count_vector_primitives(page: fitz.Page) -> tuple[int, int, int]:
        """Count horizontal/vertical vector lines and rectangles."""

        horizontal_lines = 0
        vertical_lines = 0
        rect_count = 0

        for drawing in page.get_drawings():
            for item in drawing.get("items", []):
                op = item[0]
                if op == "l":
                    start = item[1]
                    end = item[2]
                    dx = abs(end.x - start.x)
                    dy = abs(end.y - start.y)
                    if dx >= 20 and dy <= 1.5:
                        horizontal_lines += 1
                    elif dy >= 20 and dx <= 1.5:
                        vertical_lines += 1
                elif op == "re":
                    rect = item[1]
                    if rect.width >= 20 and rect.height >= 20:
                        rect_count += 1

        return horizontal_lines, vertical_lines, rect_count

    @staticmethod
    def _compute_text_features(
        page: fitz.Page,
        words: list[tuple[float, float, float, float, str, int, int, int]],
    ) -> tuple[int, int, float, int, float]:
        """Compute simple lexical/layout features with O(n) complexity."""

        words_count = len(words)
        chars_count = 0
        short_tokens = 0
        x_bins: set[int] = set()

        for w in words:
            token = w[4] if len(w) > 4 else ""
            token_len = len(token)
            chars_count += token_len
            if 0 < token_len <= 3:
                short_tokens += 1
            x_bins.add(int(w[0] // 25))

        page_area = max(page.rect.width * page.rect.height, 1.0)
        char_density = chars_count / page_area
        short_token_ratio = (short_tokens / words_count) if words_count > 0 else 0.0

        return words_count, chars_count, short_token_ratio, len(x_bins), char_density

    def _detect_visual_keyword(self, page: fitz.Page) -> tuple[bool, str | None]:
        """Detect table/figure anchors in the page header."""

        header_height = page.rect.height * 0.25
        header_clip = fitz.Rect(0, 0, page.rect.width, header_height)
        header_text = page.get_text("text", clip=header_clip).lower()

        for keyword in self.VISUAL_KEYWORDS:
            if keyword in header_text:
                return True, keyword

        return False, None

    @staticmethod
    def _detect_table_regions(page: fitz.Page) -> list[fitz.Rect]:
        """Detect table regions using PyMuPDF table finder when available."""

        if not hasattr(page, "find_tables"):
            return []

        try:
            tables = page.find_tables()
        except Exception:
            return []

        bboxes: list[fitz.Rect] = []
        for table in getattr(tables, "tables", []):
            bbox = getattr(table, "bbox", None)
            if bbox is None:
                continue
            rect = fitz.Rect(bbox)
            if rect.width > 30 and rect.height > 30:
                bboxes.append(rect)

        return bboxes

    @staticmethod
    def page_bbox_metadata(page: fitz.Page, region: fitz.Rect | None = None) -> dict[str, Any]:
        """Return serializable bbox metadata for frontend highlights."""

        rect = region or page.rect
        return {
            "x0": float(rect.x0),
            "y0": float(rect.y0),
            "x1": float(rect.x1),
            "y1": float(rect.y1),
            "width": float(rect.width),
            "height": float(rect.height),
        }
