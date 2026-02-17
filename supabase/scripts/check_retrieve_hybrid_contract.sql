-- P0.1 - Diagnostico determinista de firma RPC en entorno objetivo.
select
  n.nspname as schema,
  p.proname as function,
  pg_get_function_identity_arguments(p.oid) as args
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname = 'retrieve_hybrid_optimized';

-- Verifica aplicacion de la migracion que agrega hnsw_ef_search.
select *
from supabase_migrations.schema_migrations
where version = '20260615010000';

-- Opcional: fuerza refresh de schema cache de PostgREST.
notify pgrst, 'reload schema';
