from typing import List, Dict, Optional, Any
import logging
from app.domain.knowledge_schemas import (
    RAGSearchResult,
    RetrievalIntent,
    AgentRole,
    TaskType,
    AuthorityLevel
)

import structlog

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
    AuthorityLevel.ADMINISTRATIVE: 10.0,
    AuthorityLevel.CONSTITUTION: 10.0,
    AuthorityLevel.POLICY: 5.0,
    AuthorityLevel.CANONICAL: 1.0,
    AuthorityLevel.SUPPLEMENTARY: 0.0,
}

CREATIVE_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 1.0,
    AuthorityLevel.CONSTITUTION: 1.0,
    AuthorityLevel.POLICY: 1.2,
    AuthorityLevel.CANONICAL: 1.5,
    AuthorityLevel.SUPPLEMENTARY: 2.5,
}

PEDAGOGICAL_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 2.0,
    AuthorityLevel.CONSTITUTION: 2.0,
    AuthorityLevel.POLICY: 1.5,
    AuthorityLevel.CANONICAL: 3.0,
    AuthorityLevel.SUPPLEMENTARY: 1.0,
}

INTEGRITY_WEIGHTS = {
    AuthorityLevel.ADMINISTRATIVE: 15.0,
    AuthorityLevel.CONSTITUTION: 15.0,
    AuthorityLevel.POLICY: 3.0,
    AuthorityLevel.CANONICAL: 2.0,
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
            AuthorityLevel.ADMINISTRATIVE: 20.0,
            AuthorityLevel.CONSTITUTION: 20.0,
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
            AuthorityLevel.ADMINISTRATIVE: 1.5,
            AuthorityLevel.CONSTITUTION: 1.5,
            AuthorityLevel.POLICY: 1.0,
            AuthorityLevel.CANONICAL: 2.0,
            AuthorityLevel.SUPPLEMENTARY: 1.5,
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
            AuthorityLevel.ADMINISTRATIVE: 3.0,
            AuthorityLevel.CONSTITUTION: 3.0,
            AuthorityLevel.POLICY: 1.5,
            AuthorityLevel.CANONICAL: 5.0,
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
        "exclude_zero_weight": False, # Explicitly keep all
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

class GravityReranker:
    """
    Handles authority-aware reranking by applying weights from the GRAVITY_MATRIX.
    """

    def rerank(self, results: List[RAGSearchResult], intent: RetrievalIntent) -> List[RAGSearchResult]:
        if not results:
            return []

        # 1. Resolve Config
        config = self._resolve_config(intent.role, intent.task)
        weights = config.get("weights", BALANCED_WEIGHTS)
        exclude_zero = config.get("exclude_zero_weight", False)
        
        scored_results = []

        for result in results:
            # 2. Extract Authority
            meta = result.metadata or {}
            auth_str = meta.get("authority_level")
            auth_level = self._parse_authority_level(auth_str)
            
            # 3. Base Weight
            weight = weights.get(auth_level, 1.0)
            
            # 4. Boosts
            # Layer Boost
            source_layer = result.source_layer or 'global'
            layer_boost = 1.0
            if source_layer == 'personal':
                layer_boost = 1.2
            elif source_layer == 'tenant':
                layer_boost = 1.1
            
            # Constitutional Boost
            # Note: Checking for boolean true in metadata
            is_constitutional = meta.get('is_constitutional') is True
            constitutional_multiplier = 1000.0 if is_constitutional else 1.0
            
            # RAPTOR Boost
            is_summary = (meta.get('is_raptor_summary') is True) or (meta.get('is_summary') is True)
            raptor_boost = 1.5 if is_summary else 1.0

            # 5. Final Score Calculation
            original_score = result.similarity
            multiplier = weight * layer_boost * constitutional_multiplier * raptor_boost
            final_score = original_score * multiplier
            
            # 6. Exclusion Check
            if exclude_zero and weight == 0.0 and not (is_constitutional or is_summary):
                # Items with zero weight are excluded unless they are constitutional or summaries
                # that might have implied relevancy despite their source authority level.
                continue

            # Update result with new score and metadata
            # We create a copy or modify in place? Pydantic models are mutable by default.
            # Ideally return new objects or update fields.
            
            # Update metadata for observability
            result.metadata.update({
                "original_similarity": original_score,
                "gravity_weight": weight,
                "layer_boost": layer_boost,
                "constitutional_boost": constitutional_multiplier,
                "raptor_boost": raptor_boost,
                "authority_level": auth_level,
                "final_multiplier": multiplier
            })
            
            # Update similarity to final score
            result.similarity = final_score
            
            scored_results.append(result)

        # 7. Sort
        scored_results.sort(key=lambda x: x.similarity, reverse=True)
        
        # 8. Limit (Config or global default)
        # Note: The Orchestrator applies the hard cap (65). 
        # Here we might apply a soft cap from the matrix if desired, but 
        # usually we pass all ranked items to the orchestrator to decide final cut 
        # or use the matrix max_results.
        matrix_limit = config.get("max_results")
        if matrix_limit:
             scored_results = scored_results[:matrix_limit]

        return scored_results

    def _resolve_config(self, role: AgentRole, task: TaskType) -> Dict[str, Any]:
        # 1. Exact Match
        key = f"{role}::{task}"
        if key in GRAVITY_MATRIX:
            return GRAVITY_MATRIX[key]
        
        # 2. Role Fallback
        if role in GRAVITY_MATRIX:
            return GRAVITY_MATRIX[role]
            
        # 3. Default
        return GRAVITY_MATRIX["DEFAULT"]

    def _parse_authority_level(self, value: Optional[str]) -> AuthorityLevel:
        if not value:
            return AuthorityLevel.SUPPLEMENTARY
        
        try:
            # Handle potentially case-insensitive input, though Enums are strict
            # Our Pydantic model might assume strict Enum values.
            # But metadata comes from DB and might be raw string.
            return AuthorityLevel(value.lower())
        except ValueError:
            logger.warning("unknown_authority_level", value=value, fallback=AuthorityLevel.SUPPLEMENTARY)
            return AuthorityLevel.SUPPLEMENTARY
