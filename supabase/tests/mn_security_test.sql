-- ========================================================
-- GUÍA DE VERIFICACIÓN: Perímetro de Seguridad M:N (v4.0)
-- ========================================================
-- NOTA: Este script documenta las verificaciones de seguridad.
-- Para ejecutar las pruebas, debes usar usuarios reales creados
-- en tu instancia de Supabase, ya que no podemos crear usuarios
-- en auth.users desde SQL estándar.
-- ========================================================

-- ============================================================
-- VERIFICACIÓN 1: Aislamiento Institucional M:N (Learners)
-- ============================================================
-- OBJETIVO: Un profesor solo puede ver estudiantes vinculados a él
-- en teacher_student_mapping.

-- SETUP MANUAL REQUERIDO:
-- 1. Crear dos profesores (teacher1@test.com, teacher2@test.com)
-- 2. Crear un estudiante y vincularlo SOLO a teacher1 en teacher_student_mapping
-- 3. Obtener los UUIDs reales de estos usuarios

-- PRUEBA (ejecutar como teacher2):
-- SELECT * FROM public.learners WHERE id = '<student_id>';
-- RESULTADO ESPERADO: 0 filas (bloqueado por RLS de mapeo directo)

-- PRUEBA (ejecutar como teacher1):
-- SELECT * FROM public.learners WHERE id = '<student_id>';
-- RESULTADO ESPERADO: 1 fila (permitido por vinculación M:N)

-- ============================================================
-- VERIFICACIÓN 2: Visibilidad Forense de Exam Attempts
-- ============================================================
-- OBJETIVO: Un profesor solo puede ver intentos de exámenes que él creó
-- y solo de estudiantes vinculados a él en teacher_student_mapping.

-- SETUP MANUAL REQUERIDO:
-- 1. Crear un examen como teacher1
-- 2. Crear un intento de ese examen por el estudiante vinculado
-- 3. Intentar acceder como teacher2 (no vinculado)

-- PRUEBA (ejecutar como teacher2):
-- SELECT * FROM public.exam_attempts WHERE exam_id = '<exam_id>';
-- RESULTADO ESPERADO: 0 filas (bloqueado por RLS)

-- PRUEBA (ejecutar como teacher1):
-- SELECT * FROM public.exam_attempts WHERE exam_id = '<exam_id>';
-- RESULTADO ESPERADO: 1+ filas (permitido por autoría + vinculación M:N)

-- ============================================================
-- VERIFICACIÓN 3: Blindaje Anti-Cheat (config_snapshot)
-- ============================================================
-- OBJETIVO: Un estudiante NO puede leer el config_snapshot de su intento.

-- PRUEBA (ejecutar como estudiante):
-- SELECT config_snapshot FROM public.exam_attempts WHERE learner_id = auth.uid();
-- RESULTADO ESPERADO: ERROR de privilegios insuficientes

-- VERIFICACIÓN ALTERNATIVA (como admin):
-- SELECT grantee, privilege_type 
-- FROM information_schema.column_privileges 
-- WHERE table_name = 'exam_attempts' AND column_name = 'config_snapshot';
-- RESULTADO ESPERADO: Solo 'service_role' debe tener SELECT

-- ============================================================
-- VERIFICACIÓN 4: Protección de results_cache
-- ============================================================
-- OBJETIVO: Un estudiante NO puede modificar el results_cache de su intento.

-- PRUEBA (ejecutar como estudiante):
-- UPDATE public.exam_attempts 
-- SET results_cache = '{"score": 999}' 
-- WHERE learner_id = auth.uid();
-- RESULTADO ESPERADO: UPDATE exitoso pero sin efecto (bloqueado por WITH CHECK en RLS)

-- VERIFICACIÓN (como admin):
-- SELECT results_cache FROM public.exam_attempts WHERE learner_id = '<student_id>';
-- RESULTADO ESPERADO: results_cache sin cambios (valor original preservado)

-- ============================================================
-- VERIFICACIÓN 5: Telemetría Forense
-- ============================================================
-- OBJETIVO: Solo el profesor autor del examen y vinculado al estudiante
-- puede ver los logs de telemetría.

-- PRUEBA (ejecutar como teacher2, no vinculado):
-- SELECT * FROM public.telemetry_logs 
-- WHERE attempt_id IN (
--   SELECT id FROM public.exam_attempts WHERE learner_id = '<student_id>'
-- );
-- RESULTADO ESPERADO: 0 filas (bloqueado por RLS)

-- PRUEBA (ejecutar como teacher1, vinculado y autor):
-- SELECT * FROM public.telemetry_logs 
-- WHERE attempt_id IN (
--   SELECT id FROM public.exam_attempts WHERE learner_id = '<student_id>'
-- );
-- RESULTADO ESPERADO: Todos los logs del estudiante (permitido)

-- ============================================================
-- SCRIPT DE VALIDACIÓN RÁPIDA (Ejecutar como Admin)
-- ============================================================
-- Verifica que las políticas RLS estén activas:

SELECT 
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd
FROM pg_policies 
WHERE schemaname = 'public' 
AND tablename IN ('learners', 'exam_attempts', 'telemetry_logs', 'submissions')
ORDER BY tablename, policyname;
