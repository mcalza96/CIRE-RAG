-- =============================================================================
-- SEED DATA: GHOST ASSETS DEMONSTRATION
-- =============================================================================

DO $$
DECLARE
    v_asset_id UUID;
    v_course_id UUID;
    v_teacher_id UUID;
BEGIN
    -- 1. Identify a teacher to own the course
    SELECT id INTO v_teacher_id FROM public.profiles WHERE role = 'teacher' LIMIT 1;
    
    IF v_teacher_id IS NULL THEN
        RAISE NOTICE 'No teacher found. Skipping seed.';
        RETURN;
    END IF;

    -- 2. Identify a course for the subscription
    SELECT id INTO v_course_id FROM public.courses WHERE teacher_id = v_teacher_id LIMIT 1;

    IF v_course_id IS NULL THEN
        -- Create a dummy course if none exists
        INSERT INTO public.courses (title, description, teacher_id, category, level_required)
        VALUES ('Curso de Prueba Ghost', 'Demostración de suscripciones de conocimiento', v_teacher_id, 'Matemáticas', 1)
        RETURNING id INTO v_course_id;
    END IF;

    -- 3. Create a Global Asset (The National Curriculum)
    INSERT INTO public.global_assets (
        title, 
        description, 
        category, 
        tags, 
        vector_source_id, 
        content_type, 
        visual_metadata
    ) VALUES (
        'Currículo Nacional de Matemáticas 2024',
        'Lineamientos oficiales para el aprendizaje de matemáticas en educación secundaria.',
        'Normativa',
        ARRAY['matemáticas', 'oficial', 'curriculo'],
        'collection_curriculo_nacional_2024',
        'hard_constraint',
        '{"icon": "BookOpen", "color": "blue"}'::jsonb
    )
    RETURNING id INTO v_asset_id;

    -- 4. Create a Course Subscription
    INSERT INTO public.context_subscriptions (
        course_id,
        global_asset_id,
        is_active,
        settings
    ) VALUES (
        v_course_id,
        v_asset_id,
        TRUE,
        '{"priority": 0.9, "instruction_override": "Prioriza siempre el cumplimiento de los estándares nacionales en este curso."}'::jsonb
    );

    RAISE NOTICE 'Seed completed successfully.';
    RAISE NOTICE 'Course ID: %, Asset ID: %', v_course_id, v_asset_id;
END $$;
