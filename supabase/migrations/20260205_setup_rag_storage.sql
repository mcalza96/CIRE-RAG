-- Migration: Setup RAG Storage & Infrastructure (Robust)
-- Description: Creates private bucket 'course_knowledge' and upgrades 'source_documents' table.
-- Date: 2026-02-05

BEGIN;

-- 1. Create Private Bucket
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'course_knowledge', 
  'course_knowledge', 
  false, 
  20971520, -- 20MB
  ARRAY['application/pdf']
)
on conflict (id) do update set
  public = false,
  file_size_limit = 20971520,
  allowed_mime_types = ARRAY['application/pdf'];

-- 2. Upgrade Metadata Table (Safe Alter)
-- We check for column existence to be idempotent
DO $$
BEGIN
    -- Add storage_path
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'storage_path') THEN
        ALTER TABLE public.source_documents ADD COLUMN storage_path text;
    END IF;

    -- Add file_size
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'file_size') THEN
        ALTER TABLE public.source_documents ADD COLUMN file_size bigint;
    END IF;

    -- Add content_type
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'content_type') THEN
        ALTER TABLE public.source_documents ADD COLUMN content_type text;
    END IF;

    -- Add status
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'status') THEN
        ALTER TABLE public.source_documents ADD COLUMN status text NOT NULL DEFAULT 'queued';
        ALTER TABLE public.source_documents ADD CONSTRAINT source_documents_status_check CHECK (status IN ('queued', 'processing', 'ready', 'error'));
    END IF;

    -- Add error_message
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'source_documents' AND column_name = 'error_message') THEN
        ALTER TABLE public.source_documents ADD COLUMN error_message text;
    END IF;
END $$;

-- Indexes for performance
create index if not exists idx_source_documents_course_id on public.source_documents(course_id);
create index if not exists idx_source_documents_status on public.source_documents(status);

-- 3. Update RLS Policies
alter table public.source_documents enable row level security;

-- Drop old policies to ensure clean state
DROP POLICY IF EXISTS "Professors can view their course documents" ON public.source_documents;
DROP POLICY IF EXISTS "Professors can insert documents for their courses" ON public.source_documents;
DROP POLICY IF EXISTS "Professors can update their course documents" ON public.source_documents;
DROP POLICY IF EXISTS "Professors can delete their course documents" ON public.source_documents;

-- Re-create Policies
create policy "Professors can view their course documents"
on public.source_documents for select
to authenticated
using (
    exists (
        select 1 from public.courses c
        where c.id = public.source_documents.course_id
        and c.teacher_id = auth.uid()
    ) OR public.is_admin()
);

create policy "Professors can insert documents for their courses"
on public.source_documents for insert
to authenticated
with check (
    exists (
        select 1 from public.courses c
        where c.id = public.source_documents.course_id
        and c.teacher_id = auth.uid()
    ) OR public.is_admin()
);

create policy "Professors can update their course documents"
on public.source_documents for update
to authenticated
using (
    exists (
        select 1 from public.courses c
        where c.id = public.source_documents.course_id
        and c.teacher_id = auth.uid()
    ) OR public.is_admin()
)
with check (
    exists (
        select 1 from public.courses c
        where c.id = public.source_documents.course_id
        and c.teacher_id = auth.uid()
    ) OR public.is_admin()
);

create policy "Professors can delete their course documents"
on public.source_documents for delete
to authenticated
using (
    exists (
        select 1 from public.courses c
        where c.id = public.source_documents.course_id
        and c.teacher_id = auth.uid()
    ) OR public.is_admin()
);

-- 4. Storage RLS Policies (Bucket: course_knowledge)
drop policy if exists "Course Scope Isolation: Upload" on storage.objects;
drop policy if exists "Course Scope Isolation: Select" on storage.objects;
drop policy if exists "Course Scope Isolation: Delete" on storage.objects;

-- Policy: Upload
create policy "Course Scope Isolation: Upload"
on storage.objects for insert
to authenticated
with check (
    bucket_id = 'course_knowledge' AND
    (
        exists (
            select 1 from public.courses c
            where c.id = (storage.foldername(name))[1]::uuid
            and c.teacher_id = auth.uid()
        ) OR public.is_admin()
    )
);

-- Policy: Select (Download)
create policy "Course Scope Isolation: Select"
on storage.objects for select
to authenticated
using (
    bucket_id = 'course_knowledge' AND
    (
        exists (
            select 1 from public.courses c
            where c.id = (storage.foldername(name))[1]::uuid
            and c.teacher_id = auth.uid()
        ) OR public.is_admin()
    )
);

-- Policy: Delete
create policy "Course Scope Isolation: Delete"
on storage.objects for delete
to authenticated
using (
    bucket_id = 'course_knowledge' AND
    (
        exists (
            select 1 from public.courses c
            where c.id = (storage.foldername(name))[1]::uuid
            and c.teacher_id = auth.uid()
        ) OR public.is_admin()
    )
);

COMMIT;
