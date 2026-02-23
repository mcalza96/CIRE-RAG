"""Heuristic router that decides text vs visual ingestion per PDF page."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from tempfile import gettempdir
from typing import Any

import fitz
import structlog

from app.domain.ingestion.structure.structure_analyzer import PdfStructureAnalyzer

logger = structlog.get_logger(__name__)


class ProcessingStrategy(str, Enum):
    """Page-level routing strategy for ingestion."""

    TEXT_STANDARD = "text_standard"
    VISUAL_COMPLEX = "visual_complex"


class IngestionContentType(str, Enum):
    """Normalized task content type for downstream processing."""

    TEXT = "text"
    TABLE = "table"
    CHART = "chart"


@dataclass(frozen=True)
class RoutingDecision:
    """Router output for one page."""

    strategy: ProcessingStrategy
    content_type: IngestionContentType
    score: int
    reasons: list[str]
    region: fitz.Rect | None = None


@dataclass(frozen=True)
class IngestionTask:
    """Intermediate representation used by the ingestion orchestrator."""

    page_number: int
    strategy: ProcessingStrategy
    content_type: str
    raw_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualRoutingCostGuard:
    """Cost guardrails to prevent over-routing full documents to VLM."""

    max_visual_ratio: float = 0.35
    max_visual_pages: int = 12
    full_page_min_score: int = 5
    always_visual_score: int = 8


class DocumentStructureRouter:
    """Fast heuristic router optimized for millisecond-level page decisions."""

    def __init__(
        self,
        analyzer: PdfStructureAnalyzer | None = None,
        image_dpi: int = 144,
        cost_guard: VisualRoutingCostGuard | None = None,
    ) -> None:
        """Initialize router with dependency-injected analyzer."""

        self._analyzer = analyzer or PdfStructureAnalyzer()
        self._image_dpi = image_dpi
        self._cost_guard = cost_guard or VisualRoutingCostGuard()

    def analyze_page(self, page: fitz.Page) -> RoutingDecision:
        """Classify page as TEXT_STANDARD or VISUAL_COMPLEX."""

        signals = self._analyzer.extract_signals(page)
        score = 0
        reasons: list[str] = []
        region: fitz.Rect | None = None

        if signals.table_bboxes:
            score += 5
            reasons.append("table_detector")
            region = self._merge_regions(signals.table_bboxes)

        if signals.horizontal_lines >= 8 and signals.vertical_lines >= 3:
            score += 3
            reasons.append("grid_lines")
        elif signals.horizontal_lines >= 6 or signals.vertical_lines >= 4:
            score += 2
            reasons.append("vector_lines")

        if signals.rect_count >= 3:
            score += 1
            reasons.append("rectangles")

        if signals.has_visual_keyword:
            score += 2
            reasons.append(f"header_keyword:{signals.visual_keyword}")

        # Table-like lexical pattern: many short tokens spread across many columns.
        if (
            signals.words_count >= 45
            and signals.short_token_ratio >= 0.30
            and signals.distinct_x_bins >= 12
        ):
            score += 2
            reasons.append("tabular_lexical_pattern")

        # Bias toward false positives over false negatives for safety.
        strategy = (
            ProcessingStrategy.VISUAL_COMPLEX if score >= 3 else ProcessingStrategy.TEXT_STANDARD
        )

        content_type = IngestionContentType.TEXT
        if strategy == ProcessingStrategy.VISUAL_COMPLEX:
            if signals.visual_keyword in {"figura", "figure", "diagram", "diagrama"}:
                content_type = IngestionContentType.CHART
            else:
                content_type = IngestionContentType.TABLE

        return RoutingDecision(
            strategy=strategy,
            content_type=content_type,
            score=score,
            reasons=reasons,
            region=region,
        )

    def route_document(
        self, file_path: str, temp_image_dir: str | None = None
    ) -> list[IngestionTask]:
        """Create page-level ingestion tasks (text or visual)."""

        tasks: list[IngestionTask] = []
        output_dir = Path(temp_image_dir or Path(gettempdir()) / "cire_rag_visual_router")
        output_dir.mkdir(parents=True, exist_ok=True)

        with fitz.open(file_path) as doc:
            total_pages = len(doc)
            visual_budget = self._compute_visual_budget(total_pages=total_pages)
            visual_used = 0

            for page_idx in range(total_pages):
                page = doc.load_page(page_idx)
                page_number = page_idx + 1
                decision = self.analyze_page(page)
                decision = self._apply_cost_guard(
                    decision=decision,
                    visual_used=visual_used,
                    visual_budget=visual_budget,
                )

                if decision.strategy == ProcessingStrategy.VISUAL_COMPLEX:
                    visual_used += 1
                    image_path = self._render_visual_page(
                        page=page,
                        output_dir=output_dir,
                        page_number=page_number,
                        region=decision.region,
                    )
                    task = IngestionTask(
                        page_number=page_number,
                        strategy=decision.strategy,
                        content_type=decision.content_type.value,
                        raw_content=str(image_path),
                        metadata={
                            "page": page_number,
                            "bbox": self._analyzer.page_bbox_metadata(
                                page=page, region=decision.region
                            ),
                            "router_score": decision.score,
                            "router_reasons": decision.reasons,
                        },
                    )
                else:
                    # 1. Usar extracción por bloques con ordenamiento visual (Reading Order fix)
                    # Filtramos márgenes (8% superior/inferior) directamente en el router
                    page_rect = page.rect
                    y_margin = page_rect.height * 0.08

                    blocks = page.get_text("blocks", sort=True)
                    page_blocks = []

                    for b in blocks:
                        # b = (x0, y0, x1, y1, "text", block_no, block_type)
                        block_rect = fitz.Rect(b[:4])
                        block_text = b[4].replace("\x00", "").strip()

                        # Ignorar encabezados/pies de página ruidosos
                        if block_rect.y1 < y_margin or block_rect.y0 > (
                            page_rect.height - y_margin
                        ):
                            continue

                        if block_text:
                            page_blocks.append(block_text)

                    page_text = "\n\n".join(page_blocks)
                    task = IngestionTask(
                        page_number=page_number,
                        strategy=decision.strategy,
                        content_type=decision.content_type.value,
                        raw_content=page_text,
                        metadata={
                            "page": page_number,
                            "bbox": self._analyzer.page_bbox_metadata(page=page),
                            "router_score": decision.score,
                            "router_reasons": decision.reasons,
                        },
                    )

                tasks.append(task)

        logger.info(
            "document_routed",
            file_path=file_path,
            total_pages=len(tasks),
            visual_budget=self._compute_visual_budget(total_pages=len(tasks)),
            visual_pages=sum(1 for t in tasks if t.strategy == ProcessingStrategy.VISUAL_COMPLEX),
            text_pages=sum(1 for t in tasks if t.strategy == ProcessingStrategy.TEXT_STANDARD),
        )

        return tasks

    def _render_visual_page(
        self,
        page: fitz.Page,
        output_dir: Path,
        page_number: int,
        region: fitz.Rect | None,
    ) -> Path:
        """Render visual page/region as PNG for downstream VLM ingestion."""

        matrix = fitz.Matrix(self._image_dpi / 72.0, self._image_dpi / 72.0)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False, clip=region)
        output_path = output_dir / f"page_{page_number:04d}.png"
        pixmap.save(str(output_path))
        return output_path

    @staticmethod
    def _merge_regions(regions: list[fitz.Rect]) -> fitz.Rect:
        """Create a single region from one or many detected candidate boxes."""

        merged = fitz.Rect(regions[0])
        for rect in regions[1:]:
            merged.include_rect(rect)
        return merged

    def _compute_visual_budget(self, total_pages: int) -> int:
        """Compute how many pages are allowed through visual path."""

        ratio_budget = max(
            1, math.ceil(total_pages * max(0.0, min(self._cost_guard.max_visual_ratio, 1.0)))
        )
        hard_budget = max(1, self._cost_guard.max_visual_pages)
        return min(ratio_budget, hard_budget)

    def _apply_cost_guard(
        self,
        decision: RoutingDecision,
        visual_used: int,
        visual_budget: int,
    ) -> RoutingDecision:
        """Downgrade low-confidence visual pages to text when cost risk is high."""

        if decision.strategy != ProcessingStrategy.VISUAL_COMPLEX:
            return decision

        reasons = list(decision.reasons)

        if visual_used >= visual_budget and decision.score < self._cost_guard.always_visual_score:
            reasons.append("cost_guard:visual_budget_exceeded")
            return RoutingDecision(
                strategy=ProcessingStrategy.TEXT_STANDARD,
                content_type=IngestionContentType.TEXT,
                score=decision.score,
                reasons=reasons,
                region=None,
            )

        if decision.region is None and decision.score < self._cost_guard.full_page_min_score:
            reasons.append("cost_guard:full_page_low_confidence")
            return RoutingDecision(
                strategy=ProcessingStrategy.TEXT_STANDARD,
                content_type=IngestionContentType.TEXT,
                score=decision.score,
                reasons=reasons,
                region=None,
            )

        return decision
