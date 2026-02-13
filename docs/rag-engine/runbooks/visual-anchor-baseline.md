# Visual Anchor Baseline (Fase 0)

Objetivo: medir estado actual antes de optimizar cache/hash de VLM.

## KPIs minimos

- `cache_hit_rate` (visual parse)
- `parse_p50_ms` y `parse_p95_ms`
- `vlm_miss_count` por documento (`cache_miss`)
- `attempted` visual tasks por documento

## Fuente de datos

- `source_documents.metadata.visual_anchor` (persistido al finalizar ingesta)
- logs estructurados:
  - `visual_extraction_cache_hit`
  - `visual_extraction_cache_miss`

## Query base (Supabase SQL)

```sql
select
  date_trunc('day', updated_at) as day,
  count(*) as docs,
  coalesce(sum((metadata->'visual_anchor'->>'attempted')::int), 0) as visual_attempted,
  coalesce(sum((metadata->'visual_anchor'->>'cache_hit')::int), 0) as cache_hit,
  coalesce(sum((metadata->'visual_anchor'->>'cache_miss')::int), 0) as cache_miss,
  round(
    coalesce(sum((metadata->'visual_anchor'->>'cache_hit')::float), 0)
    / nullif(
      coalesce(sum((metadata->'visual_anchor'->>'cache_hit')::float), 0)
      + coalesce(sum((metadata->'visual_anchor'->>'cache_miss')::float), 0),
      0
    ),
    4
  ) as cache_hit_rate,
  round(avg((metadata->'visual_anchor'->>'parse_p50_ms')::float), 2) as parse_p50_ms_avg,
  round(avg((metadata->'visual_anchor'->>'parse_p95_ms')::float), 2) as parse_p95_ms_avg
from source_documents
where metadata ? 'visual_anchor'
group by 1
order by 1 desc;
```

## Baseline operativo

1. Ejecutar query para ultimos 7 dias.
2. Guardar snapshot en `docs/reports/visual-anchor-baseline-YYYY-MM-DD.md`.
3. Registrar contexto:
   - modelo ingest activo
   - provider activo
   - tamano de lote promedio
   - estado de `VISUAL_CACHE_KEY_V2_ENABLED`

## Rollout recomendado para clave de cache v2

1. Aplicar migracion SQL: `app/infrastructure/database/migrations/04_visual_cache_key_v2.sql`.
2. Verificar que `VISUAL_CACHE_KEY_V2_ENABLED=true` en entorno.
3. Ejecutar un lote pequeno y confirmar que `metadata.visual_anchor.cache_hit/cache_miss` siguen poblados.

## Fase 2 (prefetch batch de cache)

- Habilitar `VISUAL_CACHE_BATCH_PREFETCH_ENABLED=true`.
- El servicio hace lookup en lote por hash antes de llamar al parser VLM.
- Validar en logs:
  - aumento de `visual_extraction_cache_hit_prefetch`
  - reduccion de misses en `visual_extraction_cache_miss`

## Criterio de salida de Fase 0

- baseline semanal documentado con los 4 KPIs minimos;
- cifra objetivo acordada para Fase 1/2 (ejemplo: +20 puntos en hit rate, -30% p95).
