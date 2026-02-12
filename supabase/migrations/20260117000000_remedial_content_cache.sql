-- Migration: Remedial Content Cache Optimization
-- Description: Adds composite index for fast remedial content lookup and optional TTL
-- Date: 2026-01-17

-- 1. Add composite index for fast remedial content lookup
-- This enables O(1) lookup for: "Give me remedial content for competency X"
CREATE INDEX IF NOT EXISTS item_bank_remedial_lookup_idx 
ON public.item_bank(competency_id, ((metadata->>'is_remedial')::boolean))
WHERE (metadata->>'is_remedial')::boolean = true;

-- 2. Add optional TTL column for cache invalidation
-- Allows us to expire stale remedial content (e.g., after curriculum updates)
ALTER TABLE public.item_bank 
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- 3. Add index on expires_at for cleanup jobs
CREATE INDEX IF NOT EXISTS item_bank_expires_at_idx 
ON public.item_bank(expires_at)
WHERE expires_at IS NOT NULL;

-- 4. Add helper function to clean expired content
CREATE OR REPLACE FUNCTION public.cleanup_expired_item_bank()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    DELETE FROM public.item_bank
    WHERE expires_at IS NOT NULL 
      AND expires_at < NOW();
    
    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;
    
    RETURN v_deleted_count;
END;
$$;

-- 5. Grant execute permissions
GRANT EXECUTE ON FUNCTION public.cleanup_expired_item_bank() TO service_role;

-- 6. Add comment for documentation
COMMENT ON INDEX item_bank_remedial_lookup_idx IS 
'Composite index for fast remedial content lookup by competency_id. Used by mutation-executor.ts for JIT content resolution.';

COMMENT ON COLUMN item_bank.expires_at IS 
'Optional TTL for cache invalidation. Set to NULL for permanent content. Cleanup via cleanup_expired_item_bank().';
