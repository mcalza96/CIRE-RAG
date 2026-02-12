-- Make course_id nullable in source_documents to support Global Content
ALTER TABLE public.source_documents ALTER COLUMN course_id DROP NOT NULL;
