-- ============================================================================
-- MIGRATION: CREATE VISUAL NODES (Visual Anchor RAG)
-- File: 20260212000000_create_visual_nodes.sql
-- Descripcion:
--   Crea la tabla hija para anclar representaciones visuales (tablas/diagramas)
--   a chunks de texto existentes, con soporte vectorial + JSONB + RLS.
-- ============================================================================

BEGIN;

-- 1) Extensiones requeridas
-- pgvector para embeddings y pgcrypto para gen_random_uuid().
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- 2) Tabla principal
CREATE TABLE IF NOT EXISTS public.visual_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK hacia chunk de texto padre (se agrega dinamicamente mas abajo para soportar
    -- distintos nombres historicos de tabla en ambientes legacy).
    parent_chunk_id UUID NOT NULL,

    -- Ruta al asset visual en Supabase Storage (bucket: visual_assets).
    image_storage_path TEXT NOT NULL,

    -- Texto denso utilizado para embedding semantico.
    visual_summary TEXT NOT NULL,

    -- Reconstruccion estructurada de la imagen (tabla/diagrama) en JSONB.
    -- Estructura esperada:
    -- { "markdown": "| Col1 | Col2 | ...", "metadata": { "page": 5, "type": "table" } }
    structured_reconstruction JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Embedding del visual_summary.
    -- TODO(embedding-dim): verificar modelo final:
    --   - OpenAI text-embedding-3-* => 1536
    --   - algunos flujos Gemini/local => 768/1024 segun configuracion
    -- Ajustar dimension y RPCs de busqueda si se cambia.
    summary_embedding VECTOR(1536),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Validacion minima para evitar payloads JSON invalidos.
    CONSTRAINT visual_nodes_structured_reconstruction_is_object
        CHECK (jsonb_typeof(structured_reconstruction) = 'object')
);

-- 3) FK dinamica/idempotente al chunk padre
-- TODO(parent-table): confirmar nombre canonico de la tabla padre.
-- Este bloque intenta en orden: site_pages_sections, document_chunks,
-- content_chunks, knowledge_chunks.
DO $$
DECLARE
    v_parent_table TEXT;
BEGIN
    IF to_regclass('public.site_pages_sections') IS NOT NULL THEN
        v_parent_table := 'site_pages_sections';
    ELSIF to_regclass('public.document_chunks') IS NOT NULL THEN
        v_parent_table := 'document_chunks';
    ELSIF to_regclass('public.content_chunks') IS NOT NULL THEN
        v_parent_table := 'content_chunks';
    ELSIF to_regclass('public.knowledge_chunks') IS NOT NULL THEN
        v_parent_table := 'knowledge_chunks';
    END IF;

    IF v_parent_table IS NULL THEN
        RAISE NOTICE 'TODO: No se encontro tabla padre para visual_nodes.parent_chunk_id. Verificar site_pages_sections/document_chunks (o ajustar manualmente).';
    ELSE
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'visual_nodes_parent_chunk_id_fkey'
              AND conrelid = 'public.visual_nodes'::regclass
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.visual_nodes
                 ADD CONSTRAINT visual_nodes_parent_chunk_id_fkey
                 FOREIGN KEY (parent_chunk_id)
                 REFERENCES public.%I(id)
                 ON DELETE CASCADE',
                v_parent_table
            );
        END IF;
    END IF;
END
$$;

-- 4) Indices de performance
-- 4.1 ANN vector search (cosine) para recuperacion semantica rapida.
CREATE INDEX IF NOT EXISTS idx_visual_nodes_summary_embedding_hnsw
    ON public.visual_nodes
    USING hnsw (summary_embedding vector_cosine_ops)
    WHERE summary_embedding IS NOT NULL;

-- 4.2 Join/filter por chunk padre.
CREATE INDEX IF NOT EXISTS idx_visual_nodes_parent_chunk_id
    ON public.visual_nodes (parent_chunk_id);

-- 4.3 Exploracion futura de contenido estructurado (JSONB containment/path ops).
CREATE INDEX IF NOT EXISTS idx_visual_nodes_structured_reconstruction_gin
    ON public.visual_nodes
    USING gin (structured_reconstruction);

-- 5) Mantenimiento de updated_at
CREATE OR REPLACE FUNCTION public.set_visual_nodes_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_visual_nodes_updated_at ON public.visual_nodes;
CREATE TRIGGER trg_visual_nodes_updated_at
BEFORE UPDATE ON public.visual_nodes
FOR EACH ROW
EXECUTE FUNCTION public.set_visual_nodes_updated_at();

-- 6) Seguridad (RLS)
ALTER TABLE public.visual_nodes ENABLE ROW LEVEL SECURITY;

-- Lectura publica (SELECT): habilita consumo abierto de nodos visuales.
DROP POLICY IF EXISTS "visual_nodes_public_read" ON public.visual_nodes;
CREATE POLICY "visual_nodes_public_read"
ON public.visual_nodes
FOR SELECT
TO anon, authenticated
USING (TRUE);

-- Escritura para usuarios autenticados (cliente app) y/o service_role (backend jobs).
DROP POLICY IF EXISTS "visual_nodes_authenticated_write" ON public.visual_nodes;
CREATE POLICY "visual_nodes_authenticated_write"
ON public.visual_nodes
FOR ALL
TO authenticated
USING (TRUE)
WITH CHECK (TRUE);

DROP POLICY IF EXISTS "visual_nodes_service_role_write" ON public.visual_nodes;
CREATE POLICY "visual_nodes_service_role_write"
ON public.visual_nodes
FOR ALL
TO service_role
USING (TRUE)
WITH CHECK (TRUE);

COMMIT;
