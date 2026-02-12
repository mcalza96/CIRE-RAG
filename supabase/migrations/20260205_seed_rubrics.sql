-- =============================================================================
-- MIGRATION: SEED RUBRICS (Marco para la Buena Enseñanza)
-- Description: Seeds the initial official rubric for the Normative Auditor.
-- Date: 2026-02-05
-- =============================================================================

BEGIN;

DO $$ 
DECLARE
    v_rubric_id UUID;
    v_strand_a UUID; -- Dominio A
    v_strand_b UUID; -- Dominio B
    v_strand_c UUID; -- Dominio C
    v_strand_d UUID; -- Dominio D
    
    v_crit_a1 UUID;
    v_crit_b1 UUID;
    v_crit_c1 UUID;
    v_crit_d1 UUID;
    
    v_level_insat UUID;
    v_level_basic UUID;
    v_level_comp UUID;
    v_level_dest UUID;
BEGIN

    -- 1. Create Master Rubric
    INSERT INTO public.rubrics (title, description, authority, year, education_level, is_system)
    VALUES (
        'Marco para la Buena Enseñanza 2026',
        'Estándares de desempeño docente para la evaluación y mejora continua.',
        'Ministerio de Educación',
        2026,
        'General',
        true
    )
    RETURNING id INTO v_rubric_id;

    -- 2. Create Levels (Scales)
    INSERT INTO public.rubric_levels (rubric_id, name, score_value, order_index) VALUES 
    (v_rubric_id, 'Insatisfactorio', 1, 1) RETURNING id INTO v_level_insat;
    
    INSERT INTO public.rubric_levels (rubric_id, name, score_value, order_index) VALUES 
    (v_rubric_id, 'Básico', 2, 2) RETURNING id INTO v_level_basic;
    
    INSERT INTO public.rubric_levels (rubric_id, name, score_value, order_index) VALUES 
    (v_rubric_id, 'Competente', 3, 3) RETURNING id INTO v_level_comp;
    
    INSERT INTO public.rubric_levels (rubric_id, name, score_value, order_index) VALUES 
    (v_rubric_id, 'Destacado', 4, 4) RETURNING id INTO v_level_dest;

    -- 3. Create Strands (Domains) & Criteria (Indicators) & Descriptors
    
    -- === DOMINIO A ===
    INSERT INTO public.rubric_strands (rubric_id, name, description, order_index)
    VALUES (v_rubric_id, 'Dominio A: Preparación del Proceso de Enseñanza y Aprendizaje', 'Competencias relacionadas con la planificación y dominio disciplinar.', 1)
    RETURNING id INTO v_strand_a;

    -- A.1
    INSERT INTO public.rubric_criteria (rubric_id, strand_id, name, description, order_index)
    VALUES (v_rubric_id, v_strand_a, 'A.1 Domina los conocimientos disciplinares', 'Demuestra conocimiento profundo de los contenidos y su didáctica.', 1)
    RETURNING id INTO v_crit_a1;

    -- Descriptors for A.1
    INSERT INTO public.rubric_descriptors (criteria_id, level_id, description) VALUES
    (v_crit_a1, v_level_insat, 'Comete errores conceptuales frecuentes o no domina los contenidos básicos de la disciplina.'),
    (v_crit_a1, v_level_basic, 'Conoce los conceptos básicos pero presenta dificultades para relacionarlos o explicarlos con claridad.'),
    (v_crit_a1, v_level_comp, 'Domina los contenidos de la disciplina y las estrategias didácticas para enseñarlos, estableciendo relaciones claras.'),
    (v_crit_a1, v_level_dest, 'Posee un amplio y profundo dominio disciplinar, anticipando posibles dificultades de los estudiantes y proponiendo múltiples estrategias para abordarlas.');

    -- === DOMINIO B ===
    INSERT INTO public.rubric_strands (rubric_id, name, description, order_index)
    VALUES (v_rubric_id, 'Dominio B: Creación de un Ambiente Propicio para el Aprendizaje', 'Competencias relacionadas con el clima de aula y convivencia.', 2)
    RETURNING id INTO v_strand_b;

    -- B.1
    INSERT INTO public.rubric_criteria (rubric_id, strand_id, name, description, order_index)
    VALUES (v_rubric_id, v_strand_b, 'B.1 Establece un clima de relaciones de aceptación, equidad, confianza, solidaridad y respeto', 'Fomenta interacciones positivas y respetuosas.', 1)
    RETURNING id INTO v_crit_b1;

    -- Descriptors for B.1
    INSERT INTO public.rubric_descriptors (criteria_id, level_id, description) VALUES
    (v_crit_b1, v_level_insat, 'El clima es tenso, irrespetuoso o discriminatorio. No interviene ante situaciones de conflicto.'),
    (v_crit_b1, v_level_basic, 'Mantiene un clima de respeto formal, pero las interacciones son distantes o escasas. Interviene inconsistentemente en conflictos.'),
    (v_crit_b1, v_level_comp, 'Establece un clima de confianza y respeto mutuo. Promueve la participación equitativa y valora las diferencias.'),
    (v_crit_b1, v_level_dest, 'Genera un ambiente inclusivo y estimulante donde los estudiantes se sienten valorados. Los propios estudiantes promueven el respeto y la colaboración.');

    -- === DOMINIO C ===
    INSERT INTO public.rubric_strands (rubric_id, name, description, order_index)
    VALUES (v_rubric_id, 'Dominio C: Enseñanza para el Aprendizaje de Todos los Estudiantes', 'Competencias relacionadas con la ejecución de la clase y metodologías.', 3)
    RETURNING id INTO v_strand_c;

    -- C.1
    INSERT INTO public.rubric_criteria (rubric_id, strand_id, name, description, order_index)
    VALUES (v_rubric_id, v_strand_c, 'C.1 Comunica en forma clara y precisa los objetivos de aprendizaje', 'Claridad en la transmisión de metas.', 1)
    RETURNING id INTO v_crit_c1;

    -- Descriptors for C.1
    INSERT INTO public.rubric_descriptors (criteria_id, level_id, description) VALUES
    (v_crit_c1, v_level_insat, 'No comunica objetivos o estos son confusos. Las actividades no tienen relación clara con metas de aprendizaje.'),
    (v_crit_c1, v_level_basic, 'Comunica los objetivos, pero en términos complejos o poco accesibles. No verifica comprensión.'),
    (v_crit_c1, v_level_comp, 'Comunica claramente los objetivos de aprendizaje y verifica que los estudiantes los comprendan y encuentren sentido a las actividades.'),
    (v_crit_c1, v_level_dest, 'Logra que los estudiantes se apropien de los objetivos y monitoreen su propio progreso hacia ellos.');

    -- === DOMINIO D ===
    INSERT INTO public.rubric_strands (rubric_id, name, description, order_index)
    VALUES (v_rubric_id, 'Dominio D: Responsabilidades Profesionales', 'Competencias relacionadas con el compromiso ético y desarrollo profesional.', 4)
    RETURNING id INTO v_strand_d;
    
    -- D.1
    INSERT INTO public.rubric_criteria (rubric_id, strand_id, name, description, order_index)
    VALUES (v_rubric_id, v_strand_d, 'D.1 Reflexiona sistemáticamente sobre su práctica', 'Análisis crítico del propio desempeño.', 1)
    RETURNING id INTO v_crit_d1;

    -- Descriptors for D.1
    INSERT INTO public.rubric_descriptors (criteria_id, level_id, description) VALUES
    (v_crit_d1, v_level_insat, 'No muestra reflexión crítica sobre su práctica. Atribuye dificultades exclusivamente a factores externos.'),
    (v_crit_d1, v_level_basic, 'Reflexiona ocasionalmente, pero de manera descriptiva sin profundizar en causas o consecuencias.'),
    (v_crit_d1, v_level_comp, 'Analiza críticamente su práctica identificando fortalezas y debilidades. Utiliza esta reflexión para mejorar sus estrategias.'),
    (v_crit_d1, v_level_dest, 'Mantiene una práctica reflexiva sistemática y colaborativa. Investiga e innova para resolver desafíos pedagógicos complejos.');

END $$;

COMMIT;
