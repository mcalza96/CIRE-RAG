
-- Migrations for Learning Traces (Phase 4)

CREATE TABLE IF NOT EXISTS learning_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    user_input TEXT,
    context_used TEXT,
    verdict JSONB, -- Stores the StudentEvaluation or Mastery Update
    confidence FLOAT,
    node_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for analytics
CREATE INDEX IF NOT EXISTS idx_learning_traces_thread ON learning_traces(thread_id);
CREATE INDEX IF NOT EXISTS idx_learning_traces_session ON learning_traces(session_id);
