-- Add teacher_id to source_documents to support Personal Layer in RAG
ALTER TABLE public.source_documents ADD COLUMN IF NOT EXISTS teacher_id UUID REFERENCES public.profiles(id);

-- Update Hybrid RPC to use the correct column
-- (The previous version used teacher_id which failed because it wasn't there yet)
