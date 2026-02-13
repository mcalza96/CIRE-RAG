from __future__ import annotations

import structlog

from app.core.llm import get_llm

logger = structlog.get_logger(__name__)


class GroundedAnswerService:
    """Generates final grounded answer preferring Gemini."""

    def __init__(self):
        self._llm = get_llm(capability="GENERATION", prefer_provider="gemini")

    async def generate_answer(self, query: str, context_chunks: list[str], max_chunks: int = 10) -> str:
        if not query.strip():
            return "No hay una pregunta para responder."

        if not context_chunks:
            return "No tengo informacion suficiente en el contexto para responder."

        context = "\n\n".join(context_chunks[: max(1, max_chunks)]).strip()
        system_prompt = (
            "Eres un analista factual. Responde solo con evidencia del contexto. "
            "Si no hay evidencia suficiente, dilo explicitamente. "
            "No inventes datos ni cites fuentes inexistentes."
        )
        user_prompt = (
            f"PREGUNTA:\n{query}\n\n"
            f"CONTEXTO:\n{context}\n\n"
            "Devuelve una respuesta clara y breve en espanol."
        )

        response = await self._llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        content = str(getattr(response, "content", "") or "").strip()
        if not content:
            logger.warning("grounded_answer_empty_response")
            return "No tengo informacion suficiente en el contexto para responder."
        return content
