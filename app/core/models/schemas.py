"""Pydantic schemas for provider-agnostic model outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VisualParseResult(BaseModel):
    """Deterministic structured output for visual document parsing."""

    model_config = ConfigDict(extra="forbid")

    dense_summary: str = Field(
        ...,
        description=(
            "Un resumen denso y rico en keywords para busqueda semantica. "
            "Ej: 'Tabla de especificaciones de torque para valvulas serie 500'."
        ),
    )
    markdown_content: str = Field(
        ...,
        description=(
            "Representacion Markdown fidedigna de la tabla/diagrama. "
            "Debe preservar headers, celdas vacias y jerarquia exacta."
        ),
    )
    visual_metadata: dict[str, Any] = Field(
        ...,
        description=(
            "Metadatos extraidos: titulo detectado, numero de figura, "
            "ubicacion o indicadores de legibilidad."
        ),
    )


class VerificationResult(BaseModel):
    """Binary auditor output for HEART cross-validation of VLM extractions."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(
        ...,
        description="True if no numerical or structural discrepancies were found.",
    )
    discrepancies: list[str] = Field(
        default_factory=list,
        description=(
            "List of specific discrepancies detected, e.g. "
            "'Cell B3: extracted 0.5, image shows 0.05'."
        ),
    )
