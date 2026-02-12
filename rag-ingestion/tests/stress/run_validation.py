"""
Run Validation - LLM-as-a-Judge for Adversarial Testing.
Refactored to follow SOLID principles and use centralized services.
"""
import json
import logging
import os
import sys
import asyncio
import argparse
from typing import List, Optional
from datetime import datetime

from app.domain.schemas.adversarial_schema import AdversarialTestCase
from app.domain.schemas.judgment_schema import (
    Verdict, 
    JudgmentResult, 
    ValidationReport, 
    FailedCaseDetail
)
from app.infrastructure.services.llm_judge_service import LLMJudgeService
from app.infrastructure.repositories.json_test_case_repository import JsonTestCaseRepository

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# MOCK RAG SYSTEM
# =============================================================================

class MockRAGSystem:
    """Mock RAG system for testing the validation framework."""
    def __init__(self, compliance_rate: float = 0.9):
        self.compliance_rate = compliance_rate
    
    def query(self, test_case: AdversarialTestCase) -> str:
        import random
        if random.random() < self.compliance_rate:
            return f"Según {test_case.rule_article}, {test_case.institutional_rule} [[cita:{test_case.expected_node_id or 'node-123'}]]"
        else:
            return f"Entiendo tu situación. {test_case.general_knowledge} Podrías intentar hablar con el profesor."


# =============================================================================
# VALIDATION RUNNER
# =============================================================================

class ValidationRunner:
    """Orchestrates adversarial test execution, judgment, and reporting."""
    
    def __init__(
        self,
        rag_system = None,
        judge: Optional[LLMJudgeService] = None,
        threshold: float = 95.0
    ):
        self.rag_system = rag_system or MockRAGSystem()
        self.judge = judge or LLMJudgeService()
        self.threshold = threshold
    
    async def run(
        self,
        test_cases: List[AdversarialTestCase],
        verbose: bool = True
    ) -> ValidationReport:
        results: List[JudgmentResult] = []
        failed_details: List[FailedCaseDetail] = []
        
        for i, test_case in enumerate(test_cases):
            if verbose:
                print(f"Running test {i+1}/{len(test_cases)}: {test_case.id}")
            
            # 1. Get RAG response
            rag_response = self.rag_system.query(test_case)
            
            # 2. Judge the response
            result = await self.judge.judge(test_case, rag_response)
            results.append(result)
            
            if result.verdict == Verdict.FAIL:
                failed_details.append(FailedCaseDetail(
                    testCaseId=test_case.id,
                    question=test_case.question,
                    institutionalRule=test_case.institutional_rule,
                    ragResponse=rag_response,
                    reason=result.reason
                ))
                if verbose:
                    print(f"  ❌ FAIL: {result.reason}")
            elif result.verdict == Verdict.PASS:
                if verbose:
                    print(f"  ✅ PASS")
            else:
                if verbose:
                    print(f"  ⚠️ ERROR: {result.reason}")
        
        # Calculate metrics
        total = len(results)
        passed = sum(1 for r in results if r.verdict == Verdict.PASS)
        failed = sum(1 for r in results if r.verdict == Verdict.FAIL)
        errors = sum(1 for r in results if r.verdict == Verdict.ERROR)
        
        pass_rate = (passed / total * 100) if total > 0 else 0
        
        return ValidationReport(
            totalCases=total,
            passedCases=passed,
            failedCases=failed,
            errorCases=errors,
            passRate=pass_rate,
            institutionalVictoryRate=pass_rate, # Simplified for now
            threshold=self.threshold,
            passed=pass_rate >= self.threshold,
            timestamp=datetime.utcnow().isoformat(),
            results=results,
            failedDetails=failed_details
        )

    async def run_and_report(
        self,
        test_cases: List[AdversarialTestCase],
        output_dir: str = "tests/stress/reports",
        verbose: bool = True
    ) -> int:
        report = await self.run(test_cases, verbose)
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Save JSON report using Pydantic serialization
        json_path = os.path.join(output_dir, "validation_report.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            f.write(report.model_dump_json(by_alias=True, indent=2))
        
        # Save Markdown report
        md_path = os.path.join(output_dir, "validation_report.md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(report.to_markdown())
        
        print(f"\n{'='*50}")
        print(f"VALIDATION {'PASSED ✅' if report.passed else 'FAILED ❌'}")
        print(f"Pass Rate: {report.pass_rate:.1f}%")
        print(f"Threshold: {report.threshold:.1f}%")
        print(f"Reports saved to: {output_dir}")
        print(f"{'='*50}\n")
        
        return 0 if report.passed else 1


# =============================================================================
# CLI
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Run adversarial validation")
    parser.add_argument("--cases", type=str, default="tests/stress/test_cases.json")
    parser.add_argument("--output", type=str, default="tests/stress/reports")
    parser.add_argument("--threshold", type=float, default=95.0)
    parser.add_argument("--mock-compliance", type=float, default=0.95)
    parser.add_argument("--quiet", action="store_true")
    
    args = parser.parse_args()
    
    # Ensure current dir is in PYTHONPATH for absolute imports
    sys.path.append(os.getcwd())
    
    # Load test cases
    repo = JsonTestCaseRepository(json_path=args.cases)
    cases = repo.load()
    
    if not cases:
        print(f"No test cases found in {args.cases}. Run adversarial_gen.py first.")
        sys.exit(1)
    
    runner = ValidationRunner(
        rag_system=MockRAGSystem(compliance_rate=args.mock_compliance),
        threshold=args.threshold
    )
    
    exit_code = await runner.run_and_report(
        cases,
        output_dir=args.output,
        verbose=not args.quiet
    )
    
    sys.exit(exit_code)

if __name__ == "__main__":
    asyncio.run(main())
