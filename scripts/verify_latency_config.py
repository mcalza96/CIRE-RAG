import asyncio
import time
import sys
import os

# Ensure we can import app modules
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.settings import settings
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine
from app.application.services.query_decomposer import QueryPlan, PlannedSubQuery as SubQuery

# Mock Settings mechanism to override concurrency for test
original_concurrency = settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL

class MockAtomicEngine(AtomicRetrievalEngine):
    def __init__(self):
        # Skip super init to avoid DB/Embedding connections
        pass

    async def retrieve_context(self, *args, **kwargs):
        # Simulate work
        await asyncio.sleep(0.1)
        return []

    # Re-implement dedupe to avoid dependency on real data
    def _dedupe_by_id(self, items):
        return items

async def test_concurrency_limit():
    print(f"--- Verifying Subquery Concurrency Limit ---")
    
    # Test Case 1: Low Concurrency (Should take longer)
    settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL = 2
    print(f"\n[Test 1] Max Parallel = {settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL}")
    
    engine = MockAtomicEngine()
    
    # Create a plan with 10 subqueries
    subqueries = [SubQuery(id=i, query=f"q{i}", dependency_id=None, is_deep=False) for i in range(10)]
    plan = QueryPlan(is_multihop=True, sub_queries=subqueries, execution_mode="parallel")
    
    start_time = time.perf_counter()
    await engine.retrieve_context_from_plan(query="test", plan=plan)
    duration = time.perf_counter() - start_time
    
    print(f"Executed 10 subqueries (0.1s each).")
    print(f"Total Duration: {duration:.2f}s")
    
    # Interpretation
    # Ideal parallel (unbounded): 0.1s
    # Ideal sequential: 1.0s
    # Expected with limit=2: 10/2 * 0.1 = 0.5s
    
    if 0.45 <= duration <= 0.65:
        print("✅ SUCCESS: Concurrency limit respected (approx 0.5s).")
    elif duration < 0.2:
        print("❌ FAILURE: Too fast! Concurrency likely unbounded.")
    else:
        print(f"⚠️ WARNING: Duration {duration:.2f}s is unexpected for limit=2.")

    # Test Case 2: High Concurrency (Should be faster)
    settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL = 10
    print(f"\n[Test 2] Max Parallel = {settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL}")
    
    # We need a fresh engine instance or at least reload semaphore logic?
    # actually AtomicRetrievalEngine creates semaphore INSIDE retrieve_context_from_plan
    # so changing settings object *should* work if we re-call the method.
    
    start_time = time.perf_counter()
    await engine.retrieve_context_from_plan(query="test", plan=plan)
    duration = time.perf_counter() - start_time
    
    print(f"Executed 10 subqueries (0.1s each).")
    print(f"Total Duration: {duration:.2f}s")
    
    # Expected with limit=10: 10/10 * 0.1 = 0.1s
    if duration < 0.2:
        print("✅ SUCCESS: High concurrency fast as expected.")
    else:
        print(f"❌ FAILURE: Too slow ({duration:.2f}s)! Parallelism might be broken.")

    # Restore 
    settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL = original_concurrency

def check_config_visuals():
    print(f"\n--- Checking Latency Config Values ---")
    print(f"QUERY_DECOMPOSER_TIMEOUT_MS: {settings.QUERY_DECOMPOSER_TIMEOUT_MS}")
    print(f"RETRIEVAL_MULTI_QUERY_SUBQUERY_TIMEOUT_MS: {settings.RETRIEVAL_MULTI_QUERY_SUBQUERY_TIMEOUT_MS}")
    print(f"RETRIEVAL_MULTI_QUERY_MAX_PARALLEL: {settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL}")

if __name__ == "__main__":
    check_config_visuals()
    asyncio.run(test_concurrency_limit())
