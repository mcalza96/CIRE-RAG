"""Structured synthesis curation nodes."""
from typing import List, Dict, Any, Set
import asyncio
from pydantic import BaseModel, Field

from app.workflows.curriculum.state import CurriculumState, ConceptCandidate, SelectedConcept
from app.core.tools.retrieval import RetrievalTools
from app.core.structured_generation import get_strict_engine
from app.services.knowledge.gravity_reranker import GravityReranker
from app.domain.knowledge_schemas import (
    RAGSearchResult, RetrievalIntent, AgentRole, TaskType, AuthorityLevel
)

# --- Schemas for Curator ---
class CuratedConcept(BaseModel):
    title: str = Field(..., description="Short, clear title of the concept")
    summary: str = Field(..., description="Structured summary of the concept based on the content")
    rationale: str = Field(..., description="Why this concept is relevant to the requested topic")
    linked_chunk_ids: List[str] = Field(..., description="IDs of the chunks that support this concept")

class CuratedList(BaseModel):
    concepts: List[CuratedConcept]

class SearchQueries(BaseModel):
    queries: List[str] = Field(..., description="List of 3-5 specific sub-queries to explore the topic thoroughly")

# --- Nodes ---

async def explorer_node(state: CurriculumState) -> Dict[str, Any]:
    """
    Explorer Agent: Decomposes the topic into sub-queries, executes parallel retrieval,
    and reranks results using GravityReranker to ensure high evidence value.
    """
    topic = state["topic"]
    course_level = state["course_level"]
    source_id = state["source_document_id"]
    tenant_id = state["tenant_id"]
    
    # 1. Query Decomposition (Async)
    engine = get_strict_engine()
    decomposition_prompt = f"""
    You are an expert structured synthesis designer.
    
    GOAL: Break down the topic "{topic}" into 5-8 specific sub-queries suitable for a {course_level} analysis depth.
    
    INSTRUCTIONS:
    1. Identify key sub-topics:
       - Foundational components (definitions, axioms, basic elements).
       - Core concepts (the main subject matter).
       - Advanced extensions or related topics (sequences, series, limits if applicable to the domain).
    2. Formulate specific search queries for each sub-topic.
    3. Ensure diversity to cover definitions, examples, and applications.
    """
    
    try:
        search_plan = await engine.agenerate(
            prompt=decomposition_prompt,
            schema=SearchQueries
        )
        queries = search_plan.queries
        print(f"Explorer: Generated sub-queries: {queries}")
    except Exception as e:
        print(f"Explorer: Decomposition failed, falling back to single query. Error: {e}")
        queries = [f"Concepts about '{topic}' suitable for {course_level} level"]

    # 2. Batch Embedding Optimization
    # Pre-calculating all embeddings in one call yields 3-5x performance on local models
    embedding_engine = JinaEmbeddingService.get_instance()
    print(f"Explorer: Batch embedding {len(queries)} queries...")
    await embedding_engine.embed_texts(queries, task="retrieval.query")
    
    # 3. Parallel Retrieval
    retriever = RetrievalTools(k=15)
    scope = {
        "type": "institutional", 
        "tenant_id": tenant_id,
        "filters": {"source_id": source_id}
    }
    
    all_results: List[Dict[str, Any]] = []
    
    async def fetch_query(q: str):
        try:
             # Try Institutional
            res = await retriever.retrieve(q, scope_context=scope, k=15)
            if not res:
                # Fallback Global
                global_scope = {"type": "global", "filters": {"source_id": source_id}}
                res = await retriever.retrieve(q, scope_context=global_scope, k=15)
            return res
        except Exception as query_err:
            print(f"Explorer: Query '{q}' failed: {query_err}")
            return []

    async def fetch_summaries(q: str):
        try:
            if q == topic: 
                 res = await retriever.retrieve_summaries(q, tenant_id=tenant_id, k=10)
                 for r in res:
                     r["metadata"] = r.get("metadata", {}) or {}
                     r["metadata"]["is_raptor_summary"] = True
                     r["metadata"]["is_summary"] = True
                 return res
            return []
        except Exception as sum_err:
            print(f"Explorer: Summary retrieval '{q}' failed: {sum_err}")
            return []

    # Execute all queries concurrently (now they will hit the cache!)
    tasks = [fetch_query(q) for q in queries]
    tasks.append(fetch_summaries(topic))
    
    results_list = await asyncio.gather(*tasks)
    for r in results_list:
        all_results.extend(r)
        
    # 3. Deduplication (by ID)
    unique_results: Dict[str, Dict[str, Any]] = {}
    for r in all_results:
        r_id = str(r.get("id"))
        if r_id not in unique_results:
            unique_results[r_id] = r
            
    # Convert to RAGSearchResult objects for Reranker
    rag_results: List[RAGSearchResult] = []
    for r in unique_results.values():
        rag_results.append(RAGSearchResult(
            id=str(r.get("id")),
            content=r.get("content", ""),
            similarity=r.get("similarity", 0.0),
            metadata=r.get("metadata", {}),
            score=r.get("similarity", 0.0), # Init score
            source_layer="institutional" if r.get("metadata", {}).get("tenant_id") else "global"
        ))
        
    if not rag_results:
        return {"error": "Explorer: No content found for any sub-query."}

    # 4. Gravity Reranking
    reranker = GravityReranker()
    intent = RetrievalIntent(
        query=topic,
        role=AgentRole.CONTENT_DESIGNER, # Creative/Broad intent
        task=TaskType.IDEATION,
        tenant_id=tenant_id
    )
    
    reranked_results = reranker.rerank(rag_results, intent)
    
    # Take Top 25 after reranking
    final_selection = reranked_results[:25]
    
    # Map back to State Schema
    candidates: List[ConceptCandidate] = []
    for r in final_selection:
        candidates.append({
            "chunk_id": r.id,
            "content": r.content,
            "relevance_score": r.score, # uses the Gravity adjusted score
            "metadata": r.metadata
        })
            
    print(f"Explorer: Retrieved {len(candidates)} high-quality chunks after Reranking.")
    return {"retrieved_candidates": candidates}


async def curator_node(state: CurriculumState) -> Dict[str, Any]:
    """
    Curator Agent: Selects and formats the best concepts from candidates.
    """
    candidates = state.get("retrieved_candidates", [])
    if not candidates:
        return {"error": "No candidates found by Explorer"}
        
    topic = state["topic"]
    
    # Prepare context for LLM
    docs_text = "\n\n".join([
        f"CHUNK ID: {c['chunk_id']}\nCONTENT: {c['content']}" 
        for c in candidates
    ])
    
    prompt = f"""
    You are an expert structured synthesis developer.
    
    GOAL: Identificar los conceptos clave sobre "{topic}" presentes en los fragmentos de texto proporcionados.
    
    INSTRUCTIONS:
    1. Analyze the provided text chunks.
    2. Extract distinct high-signal concepts.
    3. For each concept, provide a title, a summary, a rationale, and the list of CHUNK IDs that support it.
    4. Ignore irrelevant content.
    5. Output valid JSON matching the schema.
    """
    
    engine = get_strict_engine()
    
    try:
        result = await engine.agenerate(
            prompt=prompt,
            schema=CuratedList,
            context=docs_text
        )
        
        # Map to State
        selected = []
        for c in result.concepts:
            selected.append({
                "title": c.title,
                "summary": c.summary,
                "rationale": c.rationale,
                "linked_chunk_ids": c.linked_chunk_ids
            })
            
        return {"selected_concepts": selected}
        
    except Exception as e:
        return {"error": f"Curator failed: {str(e)}"}
