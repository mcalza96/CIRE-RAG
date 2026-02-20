import logging
import re
from typing import List, Tuple

from app.core.llm import get_llm
from app.core.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)


class SummarizationAgent:
    """
    LLM-based summarization for clusters of regulatory text.
    Uses the centralized get_llm() factory and PromptRegistry.
    """

    def __init__(self, temperature: float = 0.3):
        self.llm = get_llm(temperature=temperature, capability="SUMMARIZATION")

    @staticmethod
    def _normalize_inputs(texts: List[str]) -> List[str]:
        cleaned: List[str] = []
        for text in texts:
            if not isinstance(text, str):
                continue
            value = text.strip()
            if not value:
                continue
            cleaned.append(value)
        return cleaned

    @staticmethod
    def _looks_like_refusal(value: str) -> bool:
        text = (value or "").lower().strip()
        if not text:
            return True
        patterns = (
            r"no\s+tengo\s+un\s+texto",
            r"no\s+hay\s+texto",
            r"proporciona\s+el\s+texto",
            r"por\s+favor\s+proporciona",
            r"could\s+you\s+provide\s+the\s+text",
            r"please\s+provide\s+the\s+text",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _extractive_fallback(combined: str) -> Tuple[str, str]:
        compact = " ".join(combined.split())
        if not compact:
            return "Resumen sin contenido", "Sin contenido válido para resumir."

        summary = compact[:800].rstrip()
        if len(compact) > 800:
            summary += "..."

        words = [w for w in re.split(r"\s+", summary) if w][:10]
        title = " ".join(words).strip() or "Resumen normativo"
        return title, summary

    async def asummarize(self, texts: List[str]) -> Tuple[str, str]:
        """
        Summarize a list of texts into a single cohesive summary.
        """
        valid_texts = self._normalize_inputs(texts)
        if not valid_texts:
            return "Resumen sin contenido", "Sin contenido válido para resumir."

        combined = "\n\n---\n\n".join(valid_texts)

        # Truncate if too long
        max_chars = 12000
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n\n[... texto truncado ...]"

        # Get prompts from registry
        system_prompt, user_template = PromptRegistry.get_raptor_summarization_prompts()
        summary_prompt = user_template.format(combined_texts=combined)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": summary_prompt},
        ]

        summary_response = await self.llm.ainvoke(messages)
        summary = str(summary_response.content or "").strip()

        if self._looks_like_refusal(summary):
            title, summary = self._extractive_fallback(combined)
            logger.warning("RAPTOR summary refusal detected, using extractive fallback")
            logger.info(f"Generated summary: '{title}' ({len(summary)} chars)")
            return title, summary

        # Generate title
        title_template = PromptRegistry.get_raptor_title_prompt()
        title_prompt = title_template.format(summary=summary[:500])
        title_messages = [{"role": "user", "content": title_prompt}]

        title_response = await self.llm.ainvoke(title_messages)
        title = str(title_response.content or "").strip().strip('"').strip("'")

        if self._looks_like_refusal(title):
            fallback_title, _ = self._extractive_fallback(summary)
            title = fallback_title

        logger.info(f"Generated summary: '{title}' ({len(summary)} chars)")

        return title, summary
