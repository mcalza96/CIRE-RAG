"""HEART verification: binary cross-validation of VLM extractions.

Implements the Verificador Binario pattern from s.txt §3.3:
  Step 1: VLM extracts table → JSON
  Step 2: A second call re-examines image + JSON → SÍ/NO + error list
  Step 3: On failure, retry extraction with negative feedback.

Configurable via ENABLE_HEART_VERIFICATION env var (default: False).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.core.models.interfaces import BaseVLM, ModelAdapterError
from app.core.models.schemas import VerificationResult, VisualParseResult

logger = structlog.get_logger(__name__)


VERIFICATION_PROMPT = """Eres un auditor de control de calidad para documentos técnicos ISO.

Se te entrega:
1) Una imagen original de una tabla/diagrama técnico.
2) Un JSON que fue extraído automáticamente de esa imagen por otro modelo.

Tu tarea es comparar CADA celda, número, unidad y encabezado del JSON contra la imagen original.

Reglas:
- Presta especial atención a valores numéricos, decimales y unidades (ej: 0.05 vs 0.5, mm vs cm).
- Verifica que la estructura (filas, columnas, celdas fusionadas) sea correcta.
- Si todo coincide exactamente, responde is_valid=true con discrepancies=[].
- Si encuentras errores, responde is_valid=false y lista CADA discrepancia con formato:
  "Celda [ubicación]: extraído '[valor_json]', imagen muestra '[valor_real]'"
- No inventes errores. Solo reporta discrepancias reales y verificables.
- Responde exclusivamente en JSON válido sin backticks.
"""


class ExtractionVerifier:
    """Cross-validates VLM extraction output against the source image."""

    def __init__(
        self,
        model: BaseVLM | None = None,
        max_verification_retries: int = 1,
    ) -> None:
        self._model = model
        self._max_verification_retries = max_verification_retries

    async def verify(
        self,
        image_bytes: bytes,
        parse_result: VisualParseResult,
        model: BaseVLM | None = None,
        mime_type: str = "image/png",
    ) -> VerificationResult:
        """Run binary verification of extracted data against source image."""

        active_model = model or self._model
        if active_model is None:
            logger.warning("heart_verification_no_model", action="skipping")
            return VerificationResult(is_valid=True, discrepancies=[])

        extraction_json = json.dumps(
            {
                "dense_summary": parse_result.dense_summary,
                "markdown_content": parse_result.markdown_content,
                "visual_metadata": parse_result.visual_metadata,
            },
            ensure_ascii=False,
            indent=2,
        )

        user_prompt = (
            f"{VERIFICATION_PROMPT}\n\n"
            f"--- JSON EXTRAÍDO ---\n{extraction_json}\n"
            "--- FIN JSON ---\n\n"
            "Compara el JSON anterior con la imagen adjunta. "
            "Responde con el esquema VerificationResult."
        )

        try:
            result = await asyncio.to_thread(
                active_model.generate_structured_output,
                image_bytes,
                user_prompt,
                VerificationResult,
                mime_type,
            )

            if isinstance(result, VerificationResult):
                return result
            if isinstance(result, dict):
                return VerificationResult.model_validate(result)

            logger.warning("heart_verification_unexpected_type", type=type(result).__name__)
            return VerificationResult(is_valid=True, discrepancies=[])

        except (ModelAdapterError, ValueError, Exception) as exc:
            logger.warning("heart_verification_failed", error=str(exc))
            # On verification failure, assume valid to avoid blocking ingestion.
            return VerificationResult(is_valid=True, discrepancies=[])
