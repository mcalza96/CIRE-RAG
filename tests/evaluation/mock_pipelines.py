"""Mock pipelines for local benchmark smoke runs."""

from __future__ import annotations

from typing import Any


class VisualAnchorMockPipeline:
    """Mock visual-aware pipeline used to validate benchmark runner wiring."""

    async def answer(self, question: str, top_k: int = 10) -> dict[str, Any]:
        _ = top_k
        if "iso 10005" in question.lower() or "tabla b.1" in question.lower():
            return {
                "answer": "Segun la Tabla B.1, ISO 10005 se relaciona con los capitulos 4, 5, 6, 7 y 8.",
                "contexts": [
                    {
                        "id": "b1f2f740-aaaa-bbbb-cccc-6f4e501f2c10",
                        "content": "<visual_context id=\"b1f2f740-aaaa-bbbb-cccc-6f4e501f2c10\" type=\"table\">\n| Norma ISO | Capitulos relacionados |\n|---|---|\n| ISO 10005 | 4, 5, 6, 7, 8 |\n</visual_context>",
                    }
                ],
                "cost_usd": 0.03,
            }
        return {
            "answer": "La clausula 8.4 exige control de procesos y proveedores externos.",
            "contexts": [{"id": "text-case-1", "content": "La clausula 8.4 trata control externo."}],
            "cost_usd": 0.01,
        }


class BaselineTextMockPipeline:
    """Mock baseline pipeline without visual hydration awareness."""

    async def answer(self, question: str, top_k: int = 10) -> dict[str, Any]:
        _ = top_k
        return {
            "answer": "ISO 10005 se relaciona con algunos capitulos de la tabla.",
            "contexts": [{"id": "text-only-ctx", "content": "Resumen general de la tabla."}],
            "cost_usd": 0.01,
        }


def create_visual_pipeline() -> VisualAnchorMockPipeline:
    """Factory for visual mock pipeline."""

    return VisualAnchorMockPipeline()


def create_baseline_pipeline() -> BaselineTextMockPipeline:
    """Factory for baseline mock pipeline."""

    return BaselineTextMockPipeline()
