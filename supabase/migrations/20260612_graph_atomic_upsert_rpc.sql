BEGIN;

CREATE OR REPLACE FUNCTION public.upsert_knowledge_subgraph_atomic(
    p_tenant_id uuid,
    p_chunk_id uuid,
    p_entities jsonb DEFAULT '[]'::jsonb,
    p_relations jsonb DEFAULT '[]'::jsonb
)
RETURNS TABLE (
    nodes_upserted int,
    edges_upserted int,
    links_upserted int,
    entities_extracted int,
    relations_extracted int,
    entities_inserted int,
    entities_merged int,
    relations_inserted int,
    relations_merged int,
    errors jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_entity jsonb;
    v_relation jsonb;
    v_entity_id uuid;
    v_source_id uuid;
    v_target_id uuid;
    v_name text;
    v_type text;
    v_description text;
    v_rel_source text;
    v_rel_target text;
    v_rel_type text;
    v_rel_description text;
    v_rel_weight double precision;
    v_existing_description text;
    v_existing_weight double precision;
    v_existing_relation_id uuid;
    v_vec_text text;
    v_embedding vector(1536);
    v_link_id uuid;
    v_errors jsonb := '[]'::jsonb;
    v_name_to_id jsonb := '{}'::jsonb;
    c_entities_extracted int := 0;
    c_relations_extracted int := 0;
    c_entities_inserted int := 0;
    c_entities_merged int := 0;
    c_relations_inserted int := 0;
    c_relations_merged int := 0;
    c_links_upserted int := 0;
BEGIN
    p_entities := COALESCE(p_entities, '[]'::jsonb);
    p_relations := COALESCE(p_relations, '[]'::jsonb);

    c_entities_extracted := COALESCE(jsonb_array_length(p_entities), 0);
    c_relations_extracted := COALESCE(jsonb_array_length(p_relations), 0);

    FOR v_entity IN SELECT value FROM jsonb_array_elements(p_entities)
    LOOP
        v_entity_id := NULL;
        v_embedding := NULL;
        v_name := NULLIF(BTRIM(v_entity->>'name'), '');
        v_type := NULLIF(BTRIM(v_entity->>'type'), '');
        v_description := NULLIF(BTRIM(COALESCE(v_entity->>'description', '')), '');

        IF v_name IS NULL THEN
            v_errors := v_errors || jsonb_build_array('entity_missing_name');
            CONTINUE;
        END IF;

        IF jsonb_typeof(v_entity->'embedding') = 'array' THEN
            BEGIN
                v_vec_text := '[' || COALESCE((
                    SELECT string_agg(value, ',')
                    FROM jsonb_array_elements_text(v_entity->'embedding')
                ), '') || ']';
                IF v_vec_text <> '[]' THEN
                    v_embedding := v_vec_text::vector;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                v_embedding := NULL;
                v_errors := v_errors || jsonb_build_array('entity_invalid_embedding:' || v_name);
            END;
        END IF;

        SELECT e.id, e.description
        INTO v_entity_id, v_existing_description
        FROM public.knowledge_entities e
        WHERE e.tenant_id = p_tenant_id
          AND lower(e.name) = lower(v_name)
        LIMIT 1;

        IF v_entity_id IS NOT NULL THEN
            UPDATE public.knowledge_entities e
            SET
                type = COALESCE(v_type, e.type),
                description = CASE
                    WHEN v_description IS NULL OR v_description = '' THEN e.description
                    WHEN e.description IS NULL OR e.description = '' THEN v_description
                    WHEN position(v_description in e.description) > 0 THEN e.description
                    ELSE e.description || E'\n\n' || v_description
                END,
                embedding = COALESCE(v_embedding, e.embedding),
                updated_at = now()
            WHERE e.id = v_entity_id;
            c_entities_merged := c_entities_merged + 1;
        ELSE
            INSERT INTO public.knowledge_entities (
                tenant_id,
                name,
                type,
                description,
                embedding,
                metadata
            )
            VALUES (
                p_tenant_id,
                v_name,
                v_type,
                v_description,
                v_embedding,
                '{}'::jsonb
            )
            RETURNING id INTO v_entity_id;
            c_entities_inserted := c_entities_inserted + 1;
        END IF;

        v_name_to_id := v_name_to_id || jsonb_build_object(lower(v_name), v_entity_id::text);

        IF p_chunk_id IS NOT NULL THEN
            INSERT INTO public.knowledge_node_provenance (tenant_id, entity_id, chunk_id)
            VALUES (p_tenant_id, v_entity_id, p_chunk_id)
            ON CONFLICT (tenant_id, entity_id, chunk_id)
            DO NOTHING
            RETURNING id INTO v_link_id;

            IF v_link_id IS NOT NULL THEN
                c_links_upserted := c_links_upserted + 1;
            END IF;
            v_link_id := NULL;
        END IF;
    END LOOP;

    FOR v_relation IN SELECT value FROM jsonb_array_elements(p_relations)
    LOOP
        v_rel_source := lower(NULLIF(BTRIM(v_relation->>'source'), ''));
        v_rel_target := lower(NULLIF(BTRIM(v_relation->>'target'), ''));
        v_rel_type := upper(replace(replace(COALESCE(v_relation->>'relation_type', ''), ' ', '_'), '-', '_'));
        v_rel_description := NULLIF(BTRIM(COALESCE(v_relation->>'description', '')), '');
        v_rel_weight := GREATEST(1.0, LEAST(10.0, COALESCE((v_relation->>'weight')::double precision, 1.0)));

        IF v_rel_source IS NULL OR v_rel_target IS NULL OR v_rel_type = '' THEN
            v_errors := v_errors || jsonb_build_array('relation_invalid');
            CONTINUE;
        END IF;

        v_source_id := NULL;
        v_target_id := NULL;

        IF (v_name_to_id ? v_rel_source) THEN
            v_source_id := (v_name_to_id ->> v_rel_source)::uuid;
        END IF;
        IF (v_name_to_id ? v_rel_target) THEN
            v_target_id := (v_name_to_id ->> v_rel_target)::uuid;
        END IF;

        IF v_source_id IS NULL THEN
            SELECT e.id INTO v_source_id
            FROM public.knowledge_entities e
            WHERE e.tenant_id = p_tenant_id
              AND lower(e.name) = v_rel_source
            LIMIT 1;
        END IF;

        IF v_target_id IS NULL THEN
            SELECT e.id INTO v_target_id
            FROM public.knowledge_entities e
            WHERE e.tenant_id = p_tenant_id
              AND lower(e.name) = v_rel_target
            LIMIT 1;
        END IF;

        IF v_source_id IS NULL OR v_target_id IS NULL THEN
            v_errors := v_errors || jsonb_build_array('relation_missing_entity:' || v_rel_source || '->' || v_rel_target);
            CONTINUE;
        END IF;

        v_existing_relation_id := NULL;
        v_existing_description := NULL;
        v_existing_weight := NULL;

        SELECT r.id, r.description, r.weight
        INTO v_existing_relation_id, v_existing_description, v_existing_weight
        FROM public.knowledge_relations r
        WHERE r.tenant_id = p_tenant_id
          AND r.source_entity_id = v_source_id
          AND r.target_entity_id = v_target_id
          AND r.relation_type = v_rel_type
        LIMIT 1;

        IF v_existing_relation_id IS NOT NULL THEN
            UPDATE public.knowledge_relations r
            SET
                description = CASE
                    WHEN v_rel_description IS NULL OR v_rel_description = '' THEN r.description
                    WHEN r.description IS NULL OR r.description = '' THEN v_rel_description
                    WHEN position(v_rel_description in r.description) > 0 THEN r.description
                    ELSE r.description || E'\n\n' || v_rel_description
                END,
                weight = COALESCE(r.weight, 0) + 1,
                updated_at = now()
            WHERE r.id = v_existing_relation_id;
            c_relations_merged := c_relations_merged + 1;
        ELSE
            INSERT INTO public.knowledge_relations (
                tenant_id,
                source_entity_id,
                target_entity_id,
                relation_type,
                description,
                weight,
                metadata
            )
            VALUES (
                p_tenant_id,
                v_source_id,
                v_target_id,
                v_rel_type,
                v_rel_description,
                v_rel_weight,
                '{}'::jsonb
            );
            c_relations_inserted := c_relations_inserted + 1;
        END IF;
    END LOOP;

    RETURN QUERY
    SELECT
        (c_entities_inserted + c_entities_merged)::int AS nodes_upserted,
        (c_relations_inserted + c_relations_merged)::int AS edges_upserted,
        c_links_upserted::int AS links_upserted,
        c_entities_extracted::int AS entities_extracted,
        c_relations_extracted::int AS relations_extracted,
        c_entities_inserted::int AS entities_inserted,
        c_entities_merged::int AS entities_merged,
        c_relations_inserted::int AS relations_inserted,
        c_relations_merged::int AS relations_merged,
        v_errors AS errors;
END;
$$;

REVOKE ALL ON FUNCTION public.upsert_knowledge_subgraph_atomic(uuid, uuid, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.upsert_knowledge_subgraph_atomic(uuid, uuid, jsonb, jsonb) TO service_role;

COMMIT;
