-- Create table for AI Traces (Telemetry)
CREATE TABLE IF NOT EXISTS ai_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id UUID NOT NULL DEFAULT gen_random_uuid(), -- Correlator ID
    user_id UUID REFERENCES auth.users(id), -- Optional: Who triggered it
    course_id UUID REFERENCES courses(id), -- Optional: Context
    
    input_query TEXT,
    retrieved_chunks JSONB DEFAULT '[]'::jsonb, -- Store IDs and scores
    generated_output TEXT,
    
    citation_count INTEGER DEFAULT 0,
    
    -- Performance Metrics
    latency_ms JSONB DEFAULT '{}'::jsonb, -- { embedding: 100, retrieval: 300, generation: 2000 }
    cost_metric INTEGER DEFAULT 0, -- Estimated tokens
    
    status TEXT CHECK (status IN ('success', 'failed', 'unsafe')),
    error_message TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE ai_traces ENABLE ROW LEVEL SECURITY;

-- Policies
-- Admin can view all traces
CREATE POLICY "Admins can view all traces" ON ai_traces
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
            AND profiles.role = 'admin'
        )
    );

-- Instructors can view traces regarding their courses (if desired, optional)
-- For now, keep it Admin only for "Black Box" monitoring.
CREATE POLICY "Admins can insert traces" ON ai_traces
    FOR INSERT
    WITH CHECK (true); -- Allow system/server actions to insert (Server role bypasses RLS anyway usually, but good practice)
