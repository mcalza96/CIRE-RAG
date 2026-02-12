-- ========================================================
-- TEACHEROS - MIGRACIÓN: BACKFILL DEFAULT COHORTS (FASE 7b)
-- ========================================================
-- Descripción: Regularización de datos históricos.
-- Busca todos los cursos que no tengan ningún cohorte asociado
-- y les crea automáticamente una "Sección A".
-- ========================================================

BEGIN;

DO $$ 
DECLARE
    course_record RECORD;
    created_count INT := 0;
BEGIN
    -- Iterar sobre cursos que NO tienen registros en la tabla cohorts
    FOR course_record IN 
        SELECT c.id, c.teacher_id, c.title
        FROM public.courses c
        WHERE NOT EXISTS (
            SELECT 1 FROM public.cohorts ch 
            WHERE ch.course_id = c.id
        )
    LOOP
        -- Insertar Cohorte por Defecto
        INSERT INTO public.cohorts (
            name, 
            course_id, 
            teacher_id
        ) VALUES (
            'Sección A',
            course_record.id,
            course_record.teacher_id
        );
        
        created_count := created_count + 1;
        RAISE NOTICE 'Cohorte creado para el curso: % (ID: %)', course_record.title, course_record.id;
    END LOOP;

    RAISE NOTICE 'Migración completada. Se crearon % cohortes.', created_count;
END $$;

COMMIT;
