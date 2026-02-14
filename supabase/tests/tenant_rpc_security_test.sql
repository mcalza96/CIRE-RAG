-- Tenant RPC security checks for retrieval functions.
-- Run in a controlled environment with seeded tenant data.

-- 1) Missing tenant must fail.
DO $$
BEGIN
    PERFORM public.match_knowledge_secure(
        ARRAY_FILL(0.0::float4, ARRAY[1024])::vector(1024),
        '{}'::jsonb,
        1,
        0.1,
        NULL
    );
    RAISE EXCEPTION 'Expected TENANT_REQUIRED for match_knowledge_secure';
EXCEPTION
    WHEN SQLSTATE '22023' THEN
        NULL;
END;
$$;

-- 2) Missing tenant for paginated RPC must fail.
DO $$
BEGIN
    PERFORM public.match_knowledge_paginated(
        ARRAY_FILL(0.0::float4, ARRAY[1024])::vector(1024),
        '{}'::jsonb,
        1,
        0.1,
        NULL,
        NULL,
        NULL
    );
    RAISE EXCEPTION 'Expected TENANT_REQUIRED for match_knowledge_paginated';
EXCEPTION
    WHEN SQLSTATE '22023' THEN
        NULL;
END;
$$;

-- 3) Missing p_tenant_id for summaries RPC must fail.
DO $$
BEGIN
    PERFORM public.match_summaries(
        ARRAY_FILL(0.0::float4, ARRAY[1024])::vector(1024),
        0.1,
        1,
        NULL,
        NULL
    );
    RAISE EXCEPTION 'Expected TENANT_REQUIRED for match_summaries';
EXCEPTION
    WHEN SQLSTATE '22023' THEN
        NULL;
END;
$$;

-- 4) Positive-path check (replace with real seeded tenant UUID before running in CI):
-- SELECT count(*) FROM public.match_knowledge_secure(
--   ARRAY_FILL(0.0::float4, ARRAY[1024])::vector(1024),
--   jsonb_build_object('tenant_id', '<tenant-uuid>'),
--   5, 0.1, NULL
-- );
