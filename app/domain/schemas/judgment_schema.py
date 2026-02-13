from enum import Enum
from typing import List, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

class Verdict(str, Enum):
    """Judgment verdict."""
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"

class JudgmentResult(BaseModel):
    """Result of a single test case judgment."""
    test_case_id: str = Field(alias="testCaseId")
    verdict: Verdict
    reason: str
    cited_rule: bool = Field(alias="citedRule")
    used_general_knowledge: bool = Field(alias="usedGeneralKnowledge")
    confidence: float
    rag_response: str = Field(alias="ragResponse")
    execution_time_ms: int = Field(default=0, alias="executionTimeMs")

    model_config = ConfigDict(populate_by_name=True)

class FailedCaseDetail(BaseModel):
    """Detail of a failed test case for reporting."""
    test_case_id: str = Field(alias="testCaseId")
    question: str
    institutional_rule: str = Field(alias="institutionalRule")
    rag_response: str = Field(alias="ragResponse")
    reason: str

    model_config = ConfigDict(populate_by_name=True)

class ValidationReport(BaseModel):
    """Complete validation report for CI/CD."""
    total_cases: int = Field(alias="totalCases")
    passed_cases: int = Field(alias="passedCases")
    failed_cases: int = Field(alias="failedCases")
    error_cases: int = Field(alias="errorCases")
    pass_rate: float = Field(alias="passRate")
    institutional_victory_rate: float = Field(alias="institutionalVictoryRate")
    threshold: float
    passed: bool
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    results: List[JudgmentResult]
    failed_details: List[FailedCaseDetail] = Field(default_factory=list, alias="failedDetails")

    model_config = ConfigDict(populate_by_name=True)

    def to_markdown(self) -> str:
        """Generate markdown report following 'Paper & Ink' philosophy."""
        status_emoji = "✅" if self.passed else "❌"
        
        md = f"""# Adversarial Validation Report

**Status**: {status_emoji} {"PASSED" if self.passed else "FAILED"}
**Timestamp**: {self.timestamp}

## Metrics

| Metric | Value |
|--------|-------|
| Total Cases | {self.total_cases} |
| Passed | {self.passed_cases} |
| Failed | {self.failed_cases} |
| Errors | {self.error_cases} |
| **Institutional Victory Rate** | **{self.institutional_victory_rate:.1f}%** |
| Threshold | {self.threshold:.1f}% |

## Summary

{"🎉 El sistema demostró **Cero Tolerancia** a contradicciones de reglas." if self.passed else "⚠️ El sistema falló en algunos casos adversarios. Revisar ajustes."}

"""
        if self.failed_details:
            md += "## Failed Cases (Frontier Cases)\n\n"
            for detail in self.failed_details[:10]:  # Limit to 10
                md += f"""### {detail.test_case_id}

- **Question**: {detail.question}
- **Expected**: Follow institutional rule
- **Reason for Failure**: {detail.reason}

---

"""
        return md
