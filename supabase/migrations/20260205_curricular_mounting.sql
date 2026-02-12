-- 1. Add metadata columns to courses for matching
ALTER TABLE public.courses ADD COLUMN IF NOT EXISTS subject text;
ALTER TABLE public.courses ADD COLUMN IF NOT EXISTS level text;

-- 2. Create Mounts Table
CREATE TABLE IF NOT EXISTS public.course_content_mounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES public.source_documents(id) ON DELETE CASCADE,
    mount_type TEXT CHECK (mount_type IN ('automatic', 'manual_supplement')) DEFAULT 'automatic',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(course_id, document_id)
);

-- Enable RLS on mounts
ALTER TABLE public.course_content_mounts ENABLE ROW LEVEL SECURITY;

-- Mounts RLS: User can see mounts for their own courses
DROP POLICY IF EXISTS "Users can view mounts for their courses" ON public.course_content_mounts;
CREATE POLICY "Users can view mounts for their courses" ON public.course_content_mounts
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.courses
            WHERE courses.id = course_content_mounts.course_id
            AND courses.teacher_id = auth.uid()
        )
    );

-- 3. Trigger Function
CREATE OR REPLACE FUNCTION public.auto_mount_curricular_content()
RETURNS TRIGGER AS $$
BEGIN
    -- Only proceed if subject and level are present
    IF NEW.subject IS NOT NULL AND NEW.level IS NOT NULL THEN
        INSERT INTO public.course_content_mounts (course_id, document_id, mount_type)
        SELECT 
            NEW.id, 
            sd.id, 
            'automatic'
        FROM public.source_documents sd
        WHERE 
            (sd.metadata->>'is_global')::boolean = true 
            AND sd.metadata->>'subject' = NEW.subject
            AND sd.metadata->>'level' = NEW.level
        ON CONFLICT (course_id, document_id) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4. Trigger
DROP TRIGGER IF EXISTS trigger_auto_mount ON public.courses;
CREATE TRIGGER trigger_auto_mount
    AFTER INSERT ON public.courses
    FOR EACH ROW
    EXECUTE FUNCTION public.auto_mount_curricular_content();

-- 5. RLS Updates on source_documents
DROP POLICY IF EXISTS "Access to Global or Mounted Documents" ON public.source_documents;

CREATE POLICY "Access to Global or Mounted Documents" ON public.source_documents
    FOR SELECT
    TO authenticated
    USING (
        -- 1. Global Public Content
        COALESCE((metadata->>'is_global')::boolean, false) = true
        OR
        -- 2. Mounted Content via Trigger/Manual
        EXISTS (
            SELECT 1 FROM public.course_content_mounts ccm
            JOIN public.courses c ON ccm.course_id = c.id
            WHERE 
                ccm.document_id = source_documents.id
                AND c.teacher_id = auth.uid()
        )
        OR 
        -- 3. Direct Course Ownership (Private Content)
        (course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.courses c
            WHERE c.id = source_documents.course_id
            AND c.teacher_id = auth.uid()
        ))
    );

-- 6. RPC for Manual Mounting
CREATE OR REPLACE FUNCTION public.mount_document_to_course(p_course_id UUID, p_document_id UUID)
RETURNS VOID AS $$
BEGIN
    -- Check ownership of course
    IF NOT EXISTS (SELECT 1 FROM public.courses WHERE id = p_course_id AND teacher_id = auth.uid()) THEN
        RAISE EXCEPTION 'Access Denied: Not your course';
    END IF;

    -- Insert mount
    INSERT INTO public.course_content_mounts (course_id, document_id, mount_type)
    VALUES (p_course_id, p_document_id, 'manual_supplement')
    ON CONFLICT (course_id, document_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
