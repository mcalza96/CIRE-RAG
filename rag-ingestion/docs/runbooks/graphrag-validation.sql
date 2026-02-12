-- GraphRAG validation queries (Phase 5)
-- Run in Supabase SQL editor after ingestion.

-- 1) Mandatory document entities (ISO 27001 / SoA)
select id, tenant_id, name, type, created_at
from public.knowledge_entities
where lower(name) like '%declaracion de aplicabilidad%'
   or lower(name) like '%statement of applicability%'
order by created_at desc
limit 50;

-- 2) Normative relation coverage for mandatory docs
select
  r.relation_type,
  count(*) as total
from public.knowledge_relations r
join public.knowledge_entities src on src.id = r.source_entity_id
join public.knowledge_entities tgt on tgt.id = r.target_entity_id
where lower(src.name) like '%27001%'
  and (
    lower(tgt.name) like '%declaracion de aplicabilidad%'
    or lower(tgt.name) like '%statement of applicability%'
  )
group by r.relation_type
order by total desc;

-- 3) Entities without embeddings (dimension/parse degradation signal)
select
  count(*) as entities_total,
  count(*) filter (where embedding is null) as entities_without_embedding
from public.knowledge_entities;

-- 4) Provenance coverage: entity <-> chunk links
select
  count(*) as provenance_links,
  count(distinct chunk_id) as distinct_chunks,
  count(distinct entity_id) as distinct_entities
from public.knowledge_node_provenance;

-- 5) Spot-check latest extracted graph evidence per tenant
select
  e.name as entity_name,
  r.relation_type,
  e2.name as target_name,
  p.chunk_id,
  p.created_at
from public.knowledge_relations r
join public.knowledge_entities e on e.id = r.source_entity_id
join public.knowledge_entities e2 on e2.id = r.target_entity_id
left join public.knowledge_node_provenance p on p.entity_id = e.id
where e.tenant_id = '<TENANT_UUID>'
order by p.created_at desc nulls last
limit 100;
