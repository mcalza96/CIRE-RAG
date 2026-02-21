"""
Cognitive CI/CD: Champion vs Challenger
=======================================

Automates the continuous improvement of the Socratic Tutor.
1. Ingests new high-quality traces from the last 7 days.
2. Compiles a 'Challenger' model using these new examples.
3. Compares it against the current 'Production' (Champion) model.
4. Promotes the Challenger if it exceeds the Champion by a threshold.
"""

import sys
import os
import asyncio
import logging
import json
from datetime import datetime

# Add project root to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "../../"))

import dspy
from dspy.evaluate import Evaluate
from dspy.teleprompt import BootstrapFewShotWithRandomSearch
from app.infrastructure.observability.telemetry_adapter import TelemetryLoader
from app.domain.prompts.socratic import SocraticModule, SocraticSignature, validate_socratic_response
# from app.domain.optimization.train_socratic import socratic_assessment_metric # TODO: Restore when module is found

# Configuration
THRESHOLD_PERCENT = 0.02  # 2% improvement required
PROD_MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "../../app/domain/prompts/optimized_socratic.json"
)
LOG_Level = logging.INFO

logging.basicConfig(level=LOG_Level, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    logger.info("--- Starting Cognitive CI/CD Loop ---")

    # 0. Setup LM
    # Using Groq (OpenAI GPT OSS 120B) for high-quality retraining
    lm = dspy.Groq(model="openai/gpt-oss-120b", max_tokens=2000)
    dspy.settings.configure(lm=lm)

    # 1. Ingest Incremental Data (Last 7 Days)
    logger.info("Step 1: Ingesting New Data...")
    loader = TelemetryLoader()
    new_examples = await loader.load_golden_dataset(limit=200, days_back=7)

    if len(new_examples) < 10:
        logger.info("Not enough new data (<10 samples). Skipping retraining.")
        sys.exit(0)  # Neutral exit

    # Split Data
    cutoff = int(len(new_examples) * 0.8)
    trainset = new_examples[:cutoff]
    devset = new_examples[cutoff:]
    logger.info(f"Data Split: {len(trainset)} Train / {len(devset)} Dev")

    # 2. Load Champion (Current Production)
    logger.info("Step 2: Loading Champion Model...")
    champion = SocraticModule()

    if os.path.exists(PROD_MODEL_PATH):
        try:
            champion.load(PROD_MODEL_PATH)
            logger.info("Champion loaded successfully.")
        except Exception as e:
            logger.warning(f"Failed to load Champion ({e}). Treating base module as Champion.")
    else:
        logger.warning("No production model found. Treating base module as Champion.")

    # 3. Create & Compile Challenger
    logger.info("Step 3: Training Challenger...")
    teleprompter = BootstrapFewShotWithRandomSearch(
        metric=socratic_assessment_metric,
        max_bootstrapped_demos=4,
        num_candidate_programs=5,  # Reduced for CI runtime limits
        num_threads=4,
    )

    challenger = teleprompter.compile(
        student=SocraticModule(),  # Fresh instance
        trainset=trainset,
        valset=devset,
    )

    # 4. The Arena (Evaluation)
    logger.info("Step 4: The Arena (Evaluation)...")
    evaluator = Evaluate(
        devset=devset, metric=socratic_assessment_metric, num_threads=4, display_progress=False
    )

    score_champion = evaluator(champion)
    score_challenger = evaluator(challenger)

    logger.info(
        f"ARENA RESULTS: Champion={score_champion:.2f} vs Challenger={score_challenger:.2f}"
    )

    # 5. Promotion Logic
    improvement = score_challenger - score_champion
    logger.info(f"Improvement Delta: {improvement:.2f}")

    if improvement >= THRESHOLD_PERCENT:
        logger.info(">>> CHALLENGER WINS! PROMOTING TO PRODUCTION... <<<")

        # Save mechanism
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{PROD_MODEL_PATH}.{timestamp}.bak"

        # Backup old
        if os.path.exists(PROD_MODEL_PATH):
            os.rename(PROD_MODEL_PATH, backup_path)

        # Save new
        challenger.save(PROD_MODEL_PATH)
        logger.info(f"New model saved to {PROD_MODEL_PATH}")

        # In a real scenario, we might commit this file via Git here or let GH Actions handle it
        # For this script, successfully writing the file is the "Signal" to GH Actions
        sys.exit(0)
    else:
        logger.info("Challenger failed to beat threshold. Discarding.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Critical Failure: {e}")
        sys.exit(1)  # Error exit
