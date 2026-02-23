from dataclasses import dataclass, field
from typing import Any, Protocol
import structlog


class IVisualParseResult(Protocol):
    dense_summary: str
    markdown_content: str
    visual_metadata: dict[str, Any]


class IBaseVLM(Protocol): ...


@dataclass(frozen=True)
class VerificationResult:
    is_valid: bool
    discrepancies: list[str] = field(default_factory=list)


logger = structlog.get_logger(__name__)


class ExtractionVerifier:
    """
    Dummy auditor for visual extraction.
    Can be expanded with real multi-vlm or rule-based verification logic.
    """

    async def verify(
        self,
        image_bytes: bytes,
        parse_result: IVisualParseResult,
        model: IBaseVLM,
        mime_type: str = "image/png",
    ) -> VerificationResult:
        """
        Default implementation that always validates.
        """
        return VerificationResult(is_valid=True, discrepancies=[])
