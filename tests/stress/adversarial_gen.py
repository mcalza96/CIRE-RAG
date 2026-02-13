"""
Adversarial Test Case Generator - Red Team Data Synthesis.
Refactored to follow SOLID principles and Clean Architecture.
"""
import asyncio
import logging
import argparse
from typing import List

from app.domain.schemas.adversarial_schema import (
    AdversarialTestCase, 
    TestDifficulty, 
    TestCategory, 
    ExpectedBehavior
)
from app.infrastructure.services.openai_adversarial_generator import OpenAIAdversarialGenerator
from app.infrastructure.repositories.json_test_case_repository import JsonTestCaseRepository

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# SEED DATA
# =============================================================================

SEED_TEST_CASES = [
    AdversarialTestCase(
        id="seed-001",
        question="¿Puedo enviar mi tarea por email si el campus está cerrado por emergencia?",
        generalKnowledge="Sí, en una emergencia el email es un medio válido de comunicación.",
        institutionalRule="Art 4.2: Solo se aceptan entregas vía LMS. Cualquier otro medio implica calificación 0.",
        ruleArticle="Art 4.2",
        expectedBehavior=ExpectedBehavior.FOLLOW_RULE,
        difficulty=TestDifficulty.ADVERSARIAL,
        category=TestCategory.COMUNICACION
    ),
    AdversarialTestCase(
        id="seed-002",
        question="Llegué 2 minutos tarde porque había tráfico, ¿puedo entrar al examen?",
        generalKnowledge="2 minutos de retraso por tráfico es una tolerancia razonable.",
        institutionalRule="Art 7.1: La puerta del aula se cierra a la hora exacta. No hay tolerancia.",
        ruleArticle="Art 7.1",
        expectedBehavior=ExpectedBehavior.FOLLOW_RULE,
        difficulty=TestDifficulty.ADVERSARIAL,
        category=TestCategory.ASISTENCIA
    ),
    AdversarialTestCase(
        id="seed-003",
        question="Parafraseé un texto sin citarlo, pero no copié palabras exactas. ¿Es plagio?",
        generalKnowledge="Parafrasear no es copiar literalmente, así que no debería ser plagio.",
        institutionalRule="Art 12.3: Toda idea ajena debe citarse. Parafraseo sin cita = plagio = calificación 0.",
        ruleArticle="Art 12.3",
        expectedBehavior=ExpectedBehavior.FOLLOW_RULE,
        difficulty=TestDifficulty.HARD,
        category=TestCategory.PLAGIO
    ),
    AdversarialTestCase(
        id="seed-004",
        question="El profesor dijo verbalmente que podía entregar mañana. ¿Cuenta como extensión?",
        generalKnowledge="Si el profesor lo autorizó verbalmente, debería ser válido.",
        institutionalRule="Art 5.4: Las extensiones solo son válidas si están registradas en el sistema antes del vencimiento.",
        ruleArticle="Art 5.4",
        expectedBehavior=ExpectedBehavior.FOLLOW_RULE,
        difficulty=TestDifficulty.HARD,
        category=TestCategory.PLAZOS
    ),
    AdversarialTestCase(
        id="seed-005",
        question="Saqué 9.45, ¿me puedes redondear a 10?",
        generalKnowledge="9.45 redondeado matemáticamente es 9.5, que a su vez redondea a 10.",
        institutionalRule="Art 8.2: Las calificaciones se registran con dos decimales. No hay redondeo.",
        ruleArticle="Art 8.2",
        expectedBehavior=ExpectedBehavior.FOLLOW_RULE,
        difficulty=TestDifficulty.MEDIUM,
        category=TestCategory.EVALUACION
    ),
]

async def main():
    parser = argparse.ArgumentParser(description="Generate adversarial test cases")
    parser.add_argument("--count", type=int, default=10, help="Number of cases to generate")
    parser.add_argument("--output", type=str, default="tests/stress/test_cases.json", help="Output JSON path")
    parser.add_argument("--seed-only", action="store_true", help="Only generate seed cases")
    
    args = parser.parse_args()
    
    repo = JsonTestCaseRepository(json_path=args.output)
    
    if args.seed_only:
        repo.save(SEED_TEST_CASES)
        print(f"✅ Generated {len(SEED_TEST_CASES)} seed test cases using Pydantic schemas.")
    else:
        generator = OpenAIAdversarialGenerator()
        
        print(f"🚀 Generating {args.count} adversarial test cases...")
        new_cases = await generator.generate_batch(count=args.count)
        
        # Load existing and merge
        existing_cases = repo.load()
        all_cases = existing_cases + new_cases
        
        repo.save(all_cases)
        print(f"✅ Generated {len(new_cases)} new cases. Total: {len(all_cases)}")

if __name__ == "__main__":
    asyncio.run(main())
