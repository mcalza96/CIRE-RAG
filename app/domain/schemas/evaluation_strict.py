"""
Evaluation Strict Schemas - Pydantic models with strict validation for LLM outputs.

These schemas are designed for constrained decoding, ensuring the LLM
produces syntactically and semantically valid outputs every time.
Follows CIRE-RAG v2.3 naming conventions: camelCase for domain, snake_case for DB.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator, ConfigDict, AliasChoices
from enum import Enum


# =============================================================================
# ENUMS
# =============================================================================

class EvaluationStatus(str, Enum):
    """Status of an evaluation attempt."""
    SUCCESS = "success"  # Evaluation completed successfully
    INCOMPLETE = "incomplete"  # Missing information to evaluate
    ERROR = "error"  # System/processing error


class AuthorityLevel(str, Enum):
    """Authority level for cited sources."""
    CONSTITUTION = "constitution"  # Highest: rubrics, grading policies
    POLICY = "policy"  # Syllabi, calendars
    CANONICAL = "canonical"  # Canonical manuals and official references
    SUPPLEMENTARY = "supplementary"  # General resources


# =============================================================================
# CITATION SCHEMA
# =============================================================================

class Citation(BaseModel):
    """
    A reference to an institutional source used in the evaluation.
    
    The LLM must cite specific rules when justifying grades.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    sourceTitle: str = Field(
        ...,
        alias="source_title",
        description="Nombre del documento o regla citada (e.g., 'Rúbrica de Ensayo')"
    )
    
    relevantExcerpt: str = Field(
        ...,
        alias="relevant_excerpt",
        description="Fragmento específico del documento que respalda esta evaluación"
    )
    
    authorityLevel: AuthorityLevel = Field(
        default=AuthorityLevel.SUPPLEMENTARY,
        alias="authority_level",
        description="Nivel de autoridad de la fuente: constitution > policy > canonical > supplementary"
    )
    
    pageOrSection: Optional[str] = Field(
        default=None,
        alias="page_or_section",
        description="Página o sección específica (e.g., 'Art. 15', 'Página 3')"
    )


# =============================================================================
# EVALUATION RESULT SCHEMA
# =============================================================================

class CriterionScore(BaseModel):
    """Score for a single evaluation criterion."""
    model_config = ConfigDict(populate_by_name=True)
    
    criterionName: str = Field(
        ...,
        alias="criterion_name",
        description="Nombre del criterio evaluado (e.g., 'Claridad', 'Argumentación')"
    )
    
    score: int = Field(
        ...,
        ge=0,
        description="Puntaje obtenido en este criterio (debe ser >= 0)"
    )
    
    maxScore: int = Field(
        ...,
        ge=1,
        alias="max_score",
        description="Puntaje máximo posible para este criterio (debe ser >= 1)"
    )
    
    feedback: str = Field(
        ...,
        description="Retroalimentación específica para este criterio"
    )

    identifiedMisconceptions: List[str] = Field(
        default_factory=list,
        alias="identified_misconceptions",
        description="Nodos Sombra: Modelos mentales defectuosos identificados en este criterio"
    )

    analysisReasoning: str = Field(
        ...,
        alias="analysis_reasoning",
        validation_alias=AliasChoices("analysis_reasoning", "pedagogical_reasoning"),
        description="Explicacion tecnica de por que se asigno este puntaje"
    )
    
    @model_validator(mode='after')
    def validate_score_range(self) -> 'CriterionScore':
        """Ensure score doesn't exceed maxScore."""
        if self.score > self.maxScore:
            raise ValueError(f"Score ({self.score}) cannot exceed maxScore ({self.maxScore})")
        return self


class EvaluationResult(BaseModel):
    """
    Complete evaluation result from the LLM.
    
    This is the primary output schema for grading tasks.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    status: Literal["success"] = Field(
        default="success",
        description="Siempre 'success' para evaluaciones completadas"
    )
    
    totalScore: int = Field(
        ...,
        ge=0,
        alias="total_score",
        description="Puntaje total obtenido (suma de criterios)"
    )
    
    maxTotalScore: int = Field(
        ...,
        ge=1,
        alias="max_total_score",
        description="Puntaje máximo total posible"
    )
    
    percentage: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Porcentaje de logro (0-100)"
    )
    
    overallFeedback: str = Field(
        ...,
        min_length=10,
        alias="overall_feedback",
        description="Retroalimentación general sobre el trabajo evaluado"
    )

    analysisReasoning: str = Field(
        ...,
        alias="analysis_reasoning",
        validation_alias=AliasChoices("analysis_reasoning", "pedagogical_reasoning"),
        description="Razonamiento global del sistema sobre la evaluacion"
    )
    
    strengths: List[str] = Field(
        default_factory=list,
        description="Lista de fortalezas identificadas en el trabajo"
    )
    
    areasForImprovement: List[str] = Field(
        default_factory=list,
        alias="areas_for_improvement",
        description="Lista de áreas de mejora identificadas"
    )
    
    criteriaScores: List[CriterionScore] = Field(
        default_factory=list,
        alias="criteria_scores",
        description="Desglose de puntajes por criterio"
    )
    
    citations: List[Citation] = Field(
        default_factory=list,
        description="Citas de documentos institucionales que respaldan la evaluación"
    )
    
    @model_validator(mode='after')
    def validate_totals(self) -> 'EvaluationResult':
        """Validate score consistency."""
        if self.totalScore > self.maxTotalScore:
            raise ValueError(
                f"totalScore ({self.totalScore}) cannot exceed "
                f"maxTotalScore ({self.maxTotalScore})"
            )
        
        # Validate percentage matches score ratio
        expected_pct = (self.totalScore / self.maxTotalScore) * 100
        if abs(self.percentage - expected_pct) > 1.0:  # Allow 1% tolerance
            # Auto-correct instead of raising
            self.percentage = round(expected_pct, 1)
        
        return self


# =============================================================================
# EVALUATION ERROR SCHEMA
# =============================================================================

class MissingInformation(BaseModel):
    """Details about what information is missing."""
    model_config = ConfigDict(populate_by_name=True)
    
    fieldName: str = Field(
        ...,
        alias="field_name",
        description="Nombre del campo o información faltante"
    )
    
    reason: str = Field(
        ...,
        description="Por qué esta información es necesaria"
    )


class EvaluationError(BaseModel):
    """
    Structured error response when evaluation cannot be completed.
    
    Instead of returning plain text like "No puedo evaluar esto",
    the LLM returns this structured object.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    status: Literal["incomplete", "error"] = Field(
        ...,
        description="'incomplete' si falta información, 'error' si hay un problema técnico"
    )
    
    errorCode: str = Field(
        ...,
        alias="error_code",
        description="Código de error (e.g., 'MISSING_RUBRIC', 'EMPTY_SUBMISSION', 'AMBIGUOUS_CRITERIA')"
    )
    
    message: str = Field(
        ...,
        description="Explicación clara del problema en español"
    )
    
    missingInformation: List[MissingInformation] = Field(
        default_factory=list,
        alias="missing_information",
        description="Lista de información necesaria para completar la evaluación"
    )
    
    suggestedAction: Optional[str] = Field(
        default=None,
        alias="suggested_action",
        description="Acción sugerida para resolver el problema"
    )


# =============================================================================
# UNION TYPE FOR COMPLETE RESPONSE
# =============================================================================

class EvaluationResponse(BaseModel):
    """
    Union-like response that can be either a success or error.
    Use this when you want to handle both cases in one call.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    status: EvaluationStatus = Field(
        ...,
        description="Estado de la evaluación"
    )
    
    result: Optional[EvaluationResult] = Field(
        default=None,
        description="Resultado de la evaluación (solo si status='success')"
    )
    
    error: Optional[EvaluationError] = Field(
        default=None,
        description="Detalles del error (solo si status='incomplete' o 'error')"
    )
    
    @model_validator(mode='after')
    def validate_response_consistency(self) -> 'EvaluationResponse':
        """Ensure result XOR error is present based on status."""
        if self.status == EvaluationStatus.SUCCESS:
            if self.result is None:
                raise ValueError("result is required when status is 'success'")
            if self.error is not None:
                raise ValueError("error must be None when status is 'success'")
        else:
            if self.error is None:
                raise ValueError("error is required when status is not 'success'")
            if self.result is not None:
                raise ValueError("result must be None when status is not 'success'")
        
        return self
