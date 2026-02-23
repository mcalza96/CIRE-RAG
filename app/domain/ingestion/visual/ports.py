from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.ai.contracts import VisualParseResult

class IVisualIntegrator(ABC):
    @abstractmethod
    async def integrate_visual_node(
        self,
        parent_chunk_id: str,
        parent_chunk_text: str,
        image_path: str,
        parse_result: VisualParseResult,
        content_type: str,
        anchor_context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_mode: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Any:
        pass

class IVisualParser(ABC):
    @abstractmethod
    async def parse_image(
        self,
        image_path: str,
        image_bytes: Optional[bytes] = None,
        content_type: str = "table",
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> VisualParseResult:
        pass
