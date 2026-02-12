-- Migration: Create draft_exams table
-- Description: Scratchpad table for AI Architect discovery context sessions.
-- Date: 2026-01-05

CREATE TABLE IF NOT EXISTS public.draft_exams (
    lesson_id TEXT PRIMARY KEY,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Basic RLS
ALTER TABLE public.draft_exams ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users can manage their own drafts" 
ON public.draft_exams FOR ALL 
TO authenticated 
USING (true) 
WITH CHECK (true);

-- Indices
CREATE INDEX IF NOT EXISTS idx_draft_exams_updated_at ON public.draft_exams (updated_at);
