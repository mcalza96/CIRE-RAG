-- Migration: Add start_date to cohorts for chronogram tracking
-- Description: Enables time-based curricular drift calculations.

ALTER TABLE public.cohorts 
ADD COLUMN start_date TIMESTAMPTZ DEFAULT NOW() NOT NULL;

COMMENT ON COLUMN public.cohorts.start_date IS 'Fecha de inicio de la cohorte para seguimiento del cronograma curricular.';
