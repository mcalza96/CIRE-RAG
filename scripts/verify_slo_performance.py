import asyncio
import time
import sys
import os
import random
import statistics
from unittest.mock import MagicMock

# Ensure path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.settings import settings
from app.services.retrieval.atomic_engine import AtomicRetrievalEngine
from app.application.services.query_decomposer import QueryPlan, PlannedSubQuery, QueryDecomposer

# CONFIG FOR PERFORMANCE TEST
CONCURRENCY = 10 
TOTAL_REQUESTS = 100
SUBQUERIES_PER_QUERY = 3
SUBQUERY_LATENCY = 0.8 

class LoadTestEngine(AtomicRetrievalEngine):
    def __init__(self):
        # Initialize internal state required by retrieve_context
        self._hybrid_rpc_contract_checked = True
        self._hybrid_rpc_contract_status = "ok"
        self._runtime_disable_hybrid_rpc = False
        self._contract_probe_error = None
        self.last_trace = {}
        self._graph_repo = MagicMock() # Not used but avoids errors

    async def _embed_query(self, query):
        return [0.0] * 1024

    async def _resolve_source_ids(self, scope):
        return ["doc1", "doc2"]

    async def _search_hybrid_rpc(self, **kwargs):
        # Simulate real DB latency
        await asyncio.sleep(SUBQUERY_LATENCY)
        return [{"id": "r1", "content": "test", "metadata": {}, "score": 1.0}]

    async def _graph_hop(self, **kwargs):
        return []

class LoadTestDecomposer(QueryDecomposer):
    def __init__(self):
        pass
    
    async def decompose(self, query: str) -> QueryPlan:
        # Simulate realistic distribution:
        # 70% - Simple (no decomposition needed)
        # 20% - Fast Multihop (1.5s)
        # 10% - Complex Multihop (6.5s - near timeout)
        
        rand = random.random()
        if rand < 0.70:
            # Simple query optimization (SKIP DECOMPOSER)
            return QueryPlan(is_multihop=False, execution_mode="parallel", sub_queries=[])
        
        if rand < 0.90:
            await asyncio.sleep(1.5)
            sqs = [PlannedSubQuery(id=i, query=f"sub_{i}") for i in range(2)]
            return QueryPlan(is_multihop=True, execution_mode="parallel", sub_queries=sqs)
        
        # Heavy case
        await asyncio.sleep(6.5)
        sqs = [PlannedSubQuery(id=i, query=f"sub_{i}") for i in range(SUBQUERIES_PER_QUERY)]
        return QueryPlan(is_multihop=True, execution_mode="parallel", sub_queries=sqs)

async def simulate_user_request(engine, decomposer, user_id):
    start = time.perf_counter()
    try:
        # 1. Decompose & Retrieve
        plan = await decomposer.decompose(f"query_{user_id}")
        
        if not plan.sub_queries:
            # Simple case: direct retrieval
            await engine.retrieve_context(query="test")
        else:
            # Parallel Case
            await engine.retrieve_context_from_plan(query="test", plan=plan)
        
        duration = time.perf_counter() - start
        return duration, True
    except Exception as e:
        print(f"Request failed: {e}")
        return 0, False

async def run_load_test():
    engine = LoadTestEngine()
    decomposer = LoadTestDecomposer()
    
    print(f"--- RAG SLO LOAD TEST (Realistic Distribution) ---")
    print(f"Concurrent Users Limit: {CONCURRENCY}")
    print(f"Total Requests: {TOTAL_REQUESTS}")
    print(f"Subquery Parallelism: {settings.RETRIEVAL_MULTI_QUERY_MAX_PARALLEL}")
    print("--------------------------")

    results = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded_request(i):
        async with sem:
            return await simulate_user_request(engine, decomposer, i)

    tasks = [bounded_request(i) for i in range(TOTAL_REQUESTS)]
    raw_results = await asyncio.gather(*tasks)
    
    durations = [d for d, success in raw_results if success]
    failures = len(raw_results) - len(durations)
    
    if not durations:
        print("Error: No successful requests.")
        return

    p50 = statistics.median(durations)
    p95 = statistics.quantiles(durations, n=20)[18] if len(durations) >= 20 else max(durations)
    p99 = max(durations)
    avg = sum(durations) / len(durations)

    print(f"\nResults:")
    print(f"  Average: {avg:.2f}s")
    print(f"  P50:     {p50:.2f}s")
    print(f"  P95:     {p95:.2f}s")
    print(f"  P99:     {p99:.2f}s")
    print(f"  Errors:  {failures} ({failures/TOTAL_REQUESTS*100:.1f}%)")

    print(f"\nSLO Validation:")
    p50_ok = p50 < 4.0
    p95_ok = p95 < 12.0
    error_ok = failures/TOTAL_REQUESTS < 0.01

    print(f"  [P50 < 4s]: {'✅ PASS' if p50_ok else '❌ FAIL'}")
    print(f"  [P95 < 12s]: {'✅ PASS' if p95_ok else '❌ FAIL'}")
    print(f"  [Errors < 1%]: {'✅ PASS' if error_ok else '❌ FAIL'}")

    if p50_ok and p95_ok and error_ok:
        print("\n🏆 OVERALL VERDICT: PASSED")
    else:
        print("\n⚠️ OVERALL VERDICT: FAILED")

if __name__ == "__main__":
    asyncio.run(run_load_test())
