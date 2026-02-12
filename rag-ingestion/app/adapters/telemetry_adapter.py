"""
DSPy Telemetry Adapter
======================

This module implements the ETL pipeline to convert raw SQL interaction traces
into 'Golden Dataset' examples for DSPy optimizers (MIPROv2 / BootstrapFewShot).

It connects to the PostgreSQL database, filters for high-quality interactions
(feedback_score >= 4), and maps them to dspy.Example objects with strict input/output definition.
"""

import json
import logging
from typing import List, Optional, Any, Dict
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text
import dspy
from app.core.settings import settings

# Configure logger
logger = logging.getLogger(__name__)

class TelemetryLoader:
    """
    ETL Adapter to load high-quality interaction traces from SQL to DSPy.
    """
    
    def __init__(self, connection_string: Optional[str] = None):
        """
        Initialize the loader with database connection details.
        
        Args:
            connection_string: SQLAlchemy async connection string. 
                               Defaults to DATABASE_URL env var.
        """
        self.db_url = connection_string or settings.DATABASE_URL
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable is not set.")
            
        # Ensure we are using asyncpg
        if self.db_url.startswith("postgresql://"):
            self.db_url = self.db_url.replace("postgresql://", "postgresql+asyncpg://")
            
        self.engine: AsyncEngine = create_async_engine(self.db_url, echo=False)

    async def load_golden_dataset(self, limit: int = 100, days_back: int = 7) -> List[dspy.Example]:
        """
        Extracts successful interactions and transforms them into DSPy training examples.
        
        Filter Logic:
        - feedback_score >= 4 (High quality only)
        - created_at >= NOW() - INTERVAL '{days_back} days'
        - non-empty inputs/outputs
        
        Mapping:
        - input_context -> context
        - user_query -> question
        - ai_response -> answer
        
        Returns:
            List[dspy.Example]: List of training examples ready for Teleprompter.
        """
        query = text("""
            SELECT 
                input_context, 
                student_query AS user_query,
                ai_response, 
                feedback_score 
            FROM learning_traces 
            WHERE feedback_score >= 4 
            AND student_query IS NOT NULL 
            AND ai_response IS NOT NULL
            AND created_at >= NOW() - make_interval(days => :days_back)
            LIMIT :limit;
        """)

        examples: List[dspy.Example] = []

        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(query, {"limit": limit, "days_back": days_back})
                rows = result.fetchall()
                
                logger.info(f"Extracted {len(rows)} candidate traces from SQL.")

                for row in rows:
                    try:
                        # 1. Parse Context (Handle JSON extraction if needed)
                        # Assuming input_context might be a JSON string or dict
                        raw_context = row.input_context
                        context_str = ""
                        
                        if isinstance(raw_context, str):
                            try:
                                # Try to parse to see if it's valid JSON, else treat as string
                                parsed = json.loads(raw_context)
                                context_str = json.dumps(parsed) # Standardize format
                            except json.JSONDecodeError:
                                context_str = raw_context
                        elif isinstance(raw_context, (dict, list)):
                            context_str = json.dumps(raw_context)
                        elif raw_context is None:
                            context_str = ""
                            
                        # 2. Strict Mapping
                        example = dspy.Example(
                            context=context_str,
                            question=row.user_query,
                            answer=row.ai_response
                        ).with_inputs('context', 'question')
                        
                        examples.append(example)
                        
                    except Exception as e:
                        logger.warning(f"Skipping malformed row: {e}")
                        continue
                        
        except Exception as db_err:
            logger.error(f"Database error loading traces: {db_err}")
            raise db_err
        finally:
            await self.engine.dispose()
            
        logger.info(f"Successfully loaded {len(examples)} Golden Examples for DSPy.")
        return examples
