-- Migration: Item Bank Usage Tracking
-- Description: Adds RPC function to increment usage_count atomically
-- Date: 2026-01-17

CREATE OR REPLACE FUNCTION public.increment_item_usage(
    p_item_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE public.item_bank
    SET 
        usage_count = usage_count + 1,
        updated_at = NOW()
    WHERE id = p_item_id;
END;
$$;

GRANT EXECUTE ON FUNCTION public.increment_item_usage(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.increment_item_usage(UUID) TO service_role;

COMMENT ON FUNCTION public.increment_item_usage IS 
'Atomically increments usage_count for an item in item_bank. Called by mutation-executor on cache hits.';
