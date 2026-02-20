from typing import List, Dict, Optional, Any

import structlog
from app.infrastructure.settings import settings
from app.domain.knowledge_schemas import (
    RAGSearchResult,
    RetrievalIntent,
    AgentRole,
    TaskType,
    AuthorityLevel
)

logger = structlog.get_logger(__name__)

# ============================================================================
# WEIGHT PRESETS
# ============================================================================

BALANCED_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 1.0,
    AuthorityLevel.CONSTITUTION: 1.0,
    AuthorityLevel.POLICY: 1.0,
    AuthorityLevel.CANONICAL: 1.0,
    AuthorityLevel.SUPPLEMENTARY: 1.0,
}

STRICT_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 3.0,
    AuthorityLevel.CONSTITUTION: 3.0,
    AuthorityLevel.POLICY: 2.0,
    AuthorityLevel.CANONICAL: 1.0,
    AuthorityLevel.SUPPLEMENTARY: 0.0,
}

CREATIVE_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 1.0,
    AuthorityLevel.CONSTITUTION: 1.0,
    AuthorityLevel.POLICY: 1.2,
    AuthorityLevel.CANONICAL: 1.5,
    AuthorityLevel.SUPPLEMENTARY: 2.0,
}

PEDAGOGICAL_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 1.5,
    AuthorityLevel.CONSTITUTION: 1.5,
    AuthorityLevel.POLICY: 1.2,
    AuthorityLevel.CANONICAL: 2.0,
    AuthorityLevel.SUPPLEMENTARY: 1.0,
}

INTEGRITY_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 4.0,
    AuthorityLevel.CONSTITUTION: 4.0,
    AuthorityLevel.POLICY: 2.0,
    AuthorityLevel.CANONICAL: 1.5,
    AuthorityLevel.SUPPLEMENTARY: 0.1,
}

# ============================================================================
# GRAVITY MATRIX
# ============================================================================

GRAVITY_MATRIX = {
    "DEFAULT": {
        "weights": BALANCED_WEIGHTS,
        "exclude_zero_weight": True,
        "max_results": 10
    },

    # ACADEMIC AUDITOR
    f"{AgentRole.ACADEMIC_AUDITOR}::{TaskType.GRADING}": {
        "weights": STRICT_WEIGHTS,
        "exclude_zero_weight": True,
        "max_results": 5
    },
    f"{AgentRole.ACADEMIC_AUDITOR}::{TaskType.FACT_CHECKING}": {
        "weights": {
            AuthorityLevel.ADMINISTRATIVE: 5.0,
            AuthorityLevel.CONSTITUTION: 5.0,
            AuthorityLevel.POLICY: 2.0,
            AuthorityLevel.CANONICAL: 0.5,
            AuthorityLevel.SUPPLEMENTARY: 0.0,
        },
        "exclude_zero_weight": True,
        "max_results": 3
    },
    AgentRole.ACADEMIC_AUDITOR: {
        "weights": STRICT_WEIGHTS,
        "exclude_zero_weight": True
    },

    # SOCRATIC MENTOR
    f"{AgentRole.SOCRATIC_MENTOR}::{TaskType.EXPLANATION}": {
        "weights": PEDAGOGICAL_WEIGHTS,
        "max_results": 8
    },
    f"{AgentRole.SOCRATIC_MENTOR}::{TaskType.IDEATION}": {
        "weights": {
            AuthorityLevel.ADMINISTRATIVE: 1.2,
            AuthorityLevel.CONSTITUTION: 1.2,
            AuthorityLevel.POLICY: 1.0,
            AuthorityLevel.CANONICAL: 1.5,
            AuthorityLevel.SUPPLEMENTARY: 1.3,
        },
        "max_results": 12
    },
    AgentRole.SOCRATIC_MENTOR: {
        "weights": PEDAGOGICAL_WEIGHTS
    },

    # CONTENT DESIGNER
    f"{AgentRole.CONTENT_DESIGNER}::{TaskType.IDEATION}": {
        "weights": CREATIVE_WEIGHTS,
        "max_results": 20
    },
    f"{AgentRole.CONTENT_DESIGNER}::{TaskType.FACT_CHECKING}": {
        "weights": {
            AuthorityLevel.ADMINISTRATIVE: 2.5,
            AuthorityLevel.CONSTITUTION: 2.5,
            AuthorityLevel.POLICY: 1.5,
            AuthorityLevel.CANONICAL: 3.0,
            AuthorityLevel.SUPPLEMENTARY: 0.5,
        }
    },
    AgentRole.CONTENT_DESIGNER: {
        "weights": CREATIVE_WEIGHTS,
        "max_results": 15
    },

    # INTEGRITY GUARD
    f"{AgentRole.INTEGRITY_GUARD}::{TaskType.FACT_CHECKING}": {
        "weights": INTEGRITY_WEIGHTS,
        "exclude_zero_weight": False,
        "max_results": 10
    },
    f"{AgentRole.INTEGRITY_GUARD}::{TaskType.GRADING}": {
        "weights": INTEGRITY_WEIGHTS,
        "max_results": 5
    },
    AgentRole.INTEGRITY_GUARD: {
        "weights": INTEGRITY_WEIGHTS
    }
}


# Default minimum similarity score to keep a result after reranking.
_DEFAULT_MIN_SCORE_THRESHOLD = 0.10


class GravityReranker:
    """
    Authority-aware reranker.

    Applies business-rule multipliers from the GRAVITY_MATRIX on top of
    the base similarity produced by the embedding search.  Results below
    a configurable quality threshold are pruned before they reach the LLM.
    """

    def rerank(self, results: List[RAGSearchResult], intent: RetrievalIntent) -> List[RAGSearchResult]:
        if not results:
            return []

        min_score = float(
            getattr(settings, "GRAVITY_MIN_SCORE_THRESHOLD", _DEFAULT_MIN_SCORE_THRESHOLD)
            or _DEFAULT_MIN_SCORE_THRESHOLD
        )

        config = self._resolve_config(intent.role, intent.task)
        weights = config.get("weights", BALANCED_WEIGHTS)
        exclude_zero = config.get("exclude_zero_weight", False)

        scored_results: List[RAGSearchResult] = []

        for result in results:
            meta = dict(result.metadata or {})
            auth_str = meta.get("authority_level")
            auth_level = self._parse_authority_level(auth_str)

            weight = weights.get(auth_level, 1.0)

            # --- Prune: zero-weight items ---
            is_constitutional = meta.get("is_constitutional") is True
            is_summary = (meta.get("is_raptor_summary") is True) or (meta.get("is_summary") is True)
            if exclude_zero and weight == 0.0 and not (is_constitutional or is_summary):
                continue

            # --- Prune: below minimum quality threshold (on the RAW similarity) ---
            original_score = float(result.similarity or 0.0)
            if original_score < min_score:
                continue

            # --- Boosts (moderate, not score-destroying) ---
            source_layer = result.source_layer or "global"
            layer_boost = 1.0
            if source_layer == "personal":
                layer_boost = 1.15
            elif source_layer == "tenant":
                layer_boost = 1.08

            constitutional_boost = 3.0 if is_constitutional else 1.0
            raptor_boost = 1.4 if is_summary else 1.0

            multiplier = weight * layer_boost * constitutional_boost * raptor_boost
            final_score = original_score * multiplier

            # --- Build a NEW result instead of mutating the original ---
            new_meta = dict(meta)
            new_meta.update({
                "original_similarity": original_score,
                "gravity_weight": weight,
                "layer_boost": layer_boost,
                "constitutional_boost": constitutional_boost,
                "raptor_boost": raptor_boost,
                "authority_level": auth_level,
                "final_multiplier": multiplier,
            })

            scored_results.append(
                RAGSearchResult(
                    id=result.id,
                    content=result.content,
                    similarity=final_score,
                    score=final_score,
                    source_layer=result.source_layer,
                    metadata=new_meta,
                    source_id=result.source_id,
                    semantic_context=result.semantic_context,
                )
            )

        scored_results.sort(key=lambda x: x.similarity, reverse=True)

        # --- Normalize scores to [0, 1] via min-max ---
        # The multipliers reorder the results but must NOT destroy the scale.
        if len(scored_results) > 1:
            raw_scores = [r.similarity for r in scored_results]
            max_s = max(raw_scores)
            min_s = min(raw_scores)
            spread = max_s - min_s
            if spread > 0:
                for r in scored_results:
                    normalized = (r.similarity - min_s) / spread
                    r.similarity = normalized
                    r.score = normalized
                    r.metadata["gravity_normalized_score"] = normalized
            else:
                # All scores equal — assign 1.0 to all
                for r in scored_results:
                    r.similarity = 1.0
                    r.score = 1.0
        elif len(scored_results) == 1:
            scored_results[0].similarity = 1.0
            scored_results[0].score = 1.0

        matrix_limit = config.get("max_results")
        if matrix_limit:
            scored_results = scored_results[:matrix_limit]

        return scored_results

    def _resolve_config(self, role: AgentRole, task: TaskType) -> Dict[str, Any]:
        key = f"{role}::{task}"
        if key in GRAVITY_MATRIX:
            return GRAVITY_MATRIX[key]
        if role in GRAVITY_MATRIX:
            return GRAVITY_MATRIX[role]
        return GRAVITY_MATRIX["DEFAULT"]

    def _parse_authority_level(self, value: Optional[str]) -> AuthorityLevel:
        if not value:
            return AuthorityLevel.SUPPLEMENTARY
        try:
            return AuthorityLevel(value.lower())
        except ValueError:
            logger.warning("unknown_authority_level", value=value, fallback=AuthorityLevel.SUPPLEMENTARY)
            return AuthorityLevel.SUPPLEMENTARY
