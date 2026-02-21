import structlog
from typing import List, Dict, Any, Optional
from app.domain.retrieval.ports import IRetrievalRepository

logger = structlog.get_logger(__name__)

class DirectRetrievalStrategy:
    def __init__(self, repository: IRetrievalRepository):
        self.repository = repository

    async def execute(
        self, 
        query_vector: List[float], 
        query_text: str,
        filter_conditions: Dict[str, Any], 
        k: int,
        **kwargs
    ) -> List[Dict[str, Any]]:
        return await self.repository.match_knowledge(
            vector=query_vector,
            filter_conditions=filter_conditions,
            limit=kwargs.get("fetch_k", k),
            query_text=query_text
        )

class IterativeRetrievalStrategy:
    def __init__(self, repository: IRetrievalRepository):
        self.repository = repository

    async def execute(
        self, 
        query_vector: List[float], 
        query_text: str,
        filter_conditions: Dict[str, Any], 
        k: int,
        **kwargs
    ) -> List[Dict[str, Any]]:
        target_recall_k = kwargs.get("target_recall_k", 120)
        page_size = kwargs.get("page_size", 40)
        novelty_threshold = kwargs.get("novelty_threshold", 0.10)
        
        collected: List[Dict[str, Any]] = []
        seen_ids: set = set()
        cursor_score: Optional[float] = None
        cursor_id: Optional[str] = None
        page_num = 0

        while len(collected) < target_recall_k:
            page_num += 1
            batch = await self.repository.match_knowledge_paginated(
                vector=query_vector,
                filter_conditions=filter_conditions,
                limit=page_size,
                query_text=query_text,
                cursor_score=cursor_score,
                cursor_id=cursor_id,
            )

            if not batch:
                break

            new_in_batch = 0
            for r in batch:
                rid = str(r.get("id", ""))
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    collected.append(r)
                    new_in_batch += 1

            # Update cursor from last item
            last = batch[-1]
            cursor_score = last.get("similarity")
            cursor_id = str(last.get("id", ""))

            # Novelty stopping
            novelty = new_in_batch / len(batch) if batch else 0
            if novelty < novelty_threshold:
                logger.debug("novelty_stop_triggered", novelty=novelty, threshold=novelty_threshold)
                break

        return collected
