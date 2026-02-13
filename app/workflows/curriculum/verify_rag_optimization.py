import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch
from typing import List, Dict, Any

# Ensure we can import app
sys.path.append(os.getcwd())

# MOCK DEPENDENCIES BEFORE IMPORTS
sys.modules["torch"] = MagicMock()
sys.modules["transformers"] = MagicMock()

# Mock Embedding Service to avoid loading Jina/Torch
mock_embedding, mock_es = MagicMock(), MagicMock()
mock_embedding.JinaEmbeddingService = MagicMock()
sys.modules["app.services.embedding_service"] = mock_embedding
sys.modules["dspy"] = MagicMock()
sys.modules["supabase"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

from app.workflows.curriculum.nodes import explorer_node, SearchQueries
from app.workflows.curriculum.state import CurriculumState
from app.domain.knowledge_schemas import RAGSearchResult, RetrievalIntent, AgentRole, TaskType

async def verify_rag_optimization():
    print("Starting RAG Optimization Verification...")
    
    # Mock State
    state: CurriculumState = {
        "topic": "Trigonometry",
        "course_level": "Undergraduate",
        "source_document_id": "doc-123",
        "tenant_id": "tenant-456",
        "retrieved_candidates": [],
        "selected_concepts": [],
        "error": None
    }
    
    # Mocking Dependencies
    with patch("app.workflows.curriculum.nodes.get_strict_engine") as mock_get_engine, \
         patch("app.workflows.curriculum.nodes.RetrievalTools") as MockRetrievalTools, \
         patch("app.workflows.curriculum.nodes.GravityReranker") as MockGravityReranker:
        
        # 1. Mock Engine (Query Decomposition)
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine
        
        # Simulate LLM returning sub-queries
        mock_search_plan = SearchQueries(queries=[
            "Trigonometry Unit Circle",
            "Trigonometry Identities", 
            "Trigonometry Functions"
        ])
        mock_engine.generate.return_value = mock_search_plan
        
        # 2. Mock Retrieval (Parallel Search)
        mock_retriever = AsyncMock()
        MockRetrievalTools.return_value = mock_retriever
        
        # Simulate different chunks for different queries
        async def side_effect_retrieve(query, scope_context, k):
            print(f"  [Mock] Retrieving for query: '{query}'")
            if "Unit Circle" in query:
                return [{"id": "c1", "content": "Unit Circle def", "similarity": 0.8, "metadata": {"authority_level": "canonical"}}]
            elif "Identities" in query:
                return [{"id": "c2", "content": "Pythagorean Identity", "similarity": 0.75, "metadata": {"authority_level": "supplementary"}}] # Lower authority
            elif "Functions" in query:
                 # Duplicate c1 to test deduplication
                return [
                    {"id": "c1", "content": "Unit Circle def", "similarity": 0.85, "metadata": {"authority_level": "canonical"}},
                    {"id": "c3", "content": "Sine Function", "similarity": 0.9, "metadata": {"authority_level": "administrative"}} # High authority
                ]
            return []
            
        mock_retriever.retrieve.side_effect = side_effect_retrieve
        
        async def side_effect_retrieve_summaries(query, tenant_id, k):
            print(f"  [Mock] Retrieving summaries for query: '{query}'")
            if query == "Trigonometry":
                return [{
                    "id": "s1", 
                    "content": "Trigonometry Summary", 
                    "similarity": 0.82, 
                    "metadata": {"authority_level": "canonical", "is_raptor_summary": True}
                }]
            return []

        mock_retriever.retrieve_summaries.side_effect = side_effect_retrieve_summaries
        
        # 3. Mock Reranker
        mock_reranker = MagicMock()
        MockGravityReranker.return_value = mock_reranker
        
        # Simulate reranking boosting c3 and c1 over c2, and confirming s1 is present
        def side_effect_rerank(results, intent):
            print(f"  [Mock] Reranking {len(results)} results...")
            
            # Check if summary s1 is present and has correct metadata
            s1_node = next((r for r in results if r.id == "s1"), None)
            if s1_node:
                print(f"  [Mock] Found RAPTOR summary node: {s1_node.id}, Metadata: {s1_node.metadata}")
            
            # Use original sorting for consistency
            sorted_results = sorted(results, key=lambda x: x.id, reverse=True) 
            
            # Manually set scores
            for r in sorted_results:
                if r.id == "c3": r.score = 0.99
                if r.id == "c1": r.score = 0.88
                if r.id == "c2": r.score = 0.50
                if r.id == "s1": r.score = 0.95 # Higher than c1/c2 due to RAPTOR boost
                
            return sorted_results
            
        mock_reranker.rerank.side_effect = side_effect_rerank

        # --- EXECUTE ---
        result = await explorer_node(state)
        
        # --- VERIFY ---
        
        # 1. Check Query Decomposition
        mock_engine.generate.assert_called_once()
        print("\u2705 Query Decomposition called.")
        
        # 2. Check Parallel Retrieval (3 calls)
        assert mock_retriever.retrieve.call_count == 3
        print(f"\u2705 Parallel Retrieval executed {mock_retriever.retrieve.call_count} times.")

        # 2b. Check Summary Retrieval (1 call)
        mock_retriever.retrieve_summaries.assert_called_once_with("Trigonometry", tenant_id="tenant-456", k=10)
        print("\u2705 RAPTOR Retrieval executed.")
        
        # 3. Check Deduplication
        # We returned c1 twice + s1. Should appear once each.
        # Reranker was called with 'results'.
        call_args = mock_reranker.rerank.call_args
        passed_results = call_args[0][0] # First arg
        assert len(passed_results) == 4 # c1, c2, c3, s1
        unique_ids = {r.id for r in passed_results}
        assert len(unique_ids) == 4
        print(f"\u2705 Deduplication verified. IDs sent to reranker: {unique_ids}")
        
        # 4. Check Final Output
        candidates = result["retrieved_candidates"]
        assert len(candidates) == 3
        # Check if scores from reranker were preserved
        c3 = next(c for c in candidates if c['chunk_id'] == 'c3')
        assert c3['relevance_score'] == 0.99
        print(f"\u2705 Reranking integration verified. Top score: {c3['relevance_score']}")
        
        print("\n\nSUCCESS: All RAG optimization checks passed!")

if __name__ == "__main__":
    asyncio.run(verify_rag_optimization())
