from enum import Enum
from typing import Optional, Dict
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
import uuid

class TestDifficulty(str, Enum):
    """Difficulty level of adversarial test."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ADVERSARIAL = "adversarial"

class ExpectedBehavior(str, Enum):
    """Expected system behavior."""
    FOLLOW_RULE = "FOLLOW_RULE"
    REJECT_REQUEST = "REJECT_REQUEST"
    CITE_EXCEPTION = "CITE_EXCEPTION"
    APPLY_PENALTY = "APPLY_PENALTY"

class TestCategory(str, Enum):
    """Category of institutional rule being tested."""
    PLAZOS = "plazos"
    EVALUACION = "evaluacion"
    ASISTENCIA = "asistencia"
    PLAGIO = "plagio"
    EXCEPCIONES = "excepciones"
    COMUNICACION = "comunicacion"

class AdversarialTestCase(BaseModel):
    """A single adversarial test case schema."""
    id: str = Field(default_factory=lambda: f"adv-{uuid.uuid4().hex[:8]}")
    question: str
    general_knowledge: str = Field(alias="generalKnowledge")
    institutional_rule: str = Field(alias="institutionalRule")
    rule_article: str = Field(default="Art. N/A", alias="ruleArticle")
    expected_behavior: ExpectedBehavior = Field(default=ExpectedBehavior.FOLLOW_RULE, alias="expectedBehavior")
    difficulty: TestDifficulty = Field(default=TestDifficulty.MEDIUM)
    category: TestCategory = Field(default=TestCategory.EVALUACION)
    expected_node_id: Optional[str] = Field(default=None, alias="expectedNodeId")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat(), alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)
