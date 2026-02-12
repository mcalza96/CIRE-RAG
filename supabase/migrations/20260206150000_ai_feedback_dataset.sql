-- Create ENUM for feedback types
CREATE TYPE feedback_type AS ENUM ('implicit_acceptance', 'explicit_reject', 'minor_edit', 'heavy_edit');

-- Create table for AI Feedback Dataset
CREATE TABLE IF NOT EXISTS ai_feedback_dataset (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL, -- Links to the forensic log (correlation_id)
    tenant_id UUID,          -- Optional: Link to institution if needed for RLS
    original_content TEXT,   -- The initial AI output
    final_content TEXT,      -- The human-approved version
    feedback_type feedback_type NOT NULL,
    edit_distance FLOAT,     -- Levenshtein distance normalized or absolute
    similarity_score FLOAT,  -- 0 to 1 score
    created_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Index for querying by trace_id to join with logs
CREATE INDEX IF NOT EXISTS idx_ai_feedback_trace_id ON ai_feedback_dataset(trace_id);
-- Foreign Key for Integrity
ALTER TABLE ai_feedback_dataset 
    ADD CONSTRAINT fk_ai_feedback_trace_id 
    FOREIGN KEY (trace_id) REFERENCES public.generation_traces(id)
    ON DELETE CASCADE;

-- Index for querying by type for analytics
CREATE INDEX IF NOT EXISTS idx_ai_feedback_type ON ai_feedback_dataset(feedback_type);

-- RLS Policy (Internal System Table, but good practice)
ALTER TABLE ai_feedback_dataset ENABLE ROW LEVEL SECURITY;

-- Allow insert by authenticated users (teachers saving content)
CREATE POLICY "Enable insert for authenticated users" ON ai_feedback_dataset
    FOR INSERT TO authenticated
    WITH CHECK (true);

-- Allow select by admins (or specific analytics role)
-- Simplification: Allow Service Role full access, Auth users insert only.
