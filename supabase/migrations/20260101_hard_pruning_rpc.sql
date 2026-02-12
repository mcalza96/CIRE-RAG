-- RPC: Hard Pruning (Fog of War)
-- Recursively locks all downstream competencies that depend on the infected root node.

CREATE OR REPLACE FUNCTION apply_hard_pruning(
    p_learner_id UUID,
    p_root_content_id UUID
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- 1. Recursive CTE to find all descendant nodes (Transitive Closure of Dependencies)
    -- We assume edge direction: Source -> Target (Type: prerequisite) means Source is a Prerequisite for Target.
    -- If Source fails, Target is unreachable.
    
    WITH RECURSIVE downstream_graph AS (
        -- Base Case: The infected node itself (optional to lock itself, but usually we just lock descendants)
        -- We won't lock the infected node here as it's being "treated" (status infected), 
        -- we want to lock the *future* path.
        -- So start with immediate children.
        SELECT 
            target_id, 
            1 as depth
        FROM competency_edges
        WHERE source_id = p_root_content_id 
        AND relation_type = 'prerequisite'
        
        UNION
        
        -- Recursive Step: Find children of children
        SELECT 
            ce.target_id,
            dg.depth + 1
        FROM competency_edges ce
        INNER JOIN downstream_graph dg ON ce.source_id = dg.target_id
        WHERE ce.relation_type = 'prerequisite'
        -- Prevent cycles (simple depth limit or path check could be added if graph is cyclic, typical for curricula)
        AND dg.depth < 20
    )
    
    -- 2. Update Path Nodes for the learner
    -- Set status = 'locked' for all identified descendants
    UPDATE path_nodes
    SET status = 'locked'
    WHERE learner_id = p_learner_id
    AND content_id IN (SELECT target_id FROM downstream_graph)
    AND status != 'completed'; -- Don't lock already completed nodes (regression protection)
    
END;
$$;
