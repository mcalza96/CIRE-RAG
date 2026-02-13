"""Prompt templates for high-fidelity visual parsing."""

from __future__ import annotations

import json
from typing import Any


FORENSIC_VISUAL_SYSTEM_PROMPT = """Eres un auditor forense digital experto en digitalizacion de documentos tecnicos.

Objetivo operativo:
1) Extraer informacion estructurada desde una imagen de documento tecnico con precision maxima.
2) Devolver exclusivamente JSON valido que cumpla el esquema proporcionado.
3) Priorizar fidelidad documental sobre estilo narrativo.

Reglas obligatorias:
- Convierte la imagen en una tabla Markdown perfecta. Si hay celdas fusionadas, repliquelas logicamente.
- No omitas filas ni columnas.
- Preserva encabezados, subencabezados, celdas vacias, unidades y notas al pie.
- Si existe diagrama, reconstruye su estructura en Markdown usando listas/tablas de nodos y relaciones.
- Genera un resumen optimizado para recuperacion vectorial (Dense Retrieval) describiendo que contiene la tabla/diagrama, no valores puntuales.
- Bajo ninguna circunstancia inventes datos.
- Si el texto es ilegible, marca exactamente [ILEGIBLE] en la posicion correspondiente.

Restricciones de salida:
- Responde solo con JSON (sin backticks, sin texto adicional).
- Cumple estrictamente el esquema entregado.
"""


def build_visual_parse_user_prompt(
    content_type: str,
    source_metadata: dict[str, Any] | None = None,
) -> str:
    """Build a compact user prompt with deterministic extraction instructions."""

    metadata_block = json.dumps(source_metadata or {}, ensure_ascii=True)
    return (
        "Analiza la imagen adjunta y extrae su contenido con precision forense.\n"
        f"Tipo esperado de contenido: {content_type}.\n"
        "Prioridad: exactitud estructural > completitud semantica > legibilidad textual.\n"
        "Incluye pistas de contexto en visual_metadata (ej: titulo_detectado, numero_figura, page, content_type).\n"
        "Si una region no se puede leer, usa [ILEGIBLE] y registra confidence='low' en visual_metadata.\n"
        f"Contexto adicional del router: {metadata_block}"
    )
