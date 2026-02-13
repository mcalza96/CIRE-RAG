# Tenant Partitioning Plan

Objetivo: mantener latencia estable en retrieval multi-tenant cuando crece el volumen de `content_chunks`.

## Estrategia recomendada (sin downtime)

1. Crear tabla nueva particionada (`content_chunks_v2`) por hash de tenant (o por lista de tenants enterprise).
2. Migrar datos por lotes desde `content_chunks` a `content_chunks_v2` con ventana controlada.
3. Hacer dual-write temporal en ingestion (tabla vieja + nueva).
4. Cambiar RPCs de retrieval para leer `content_chunks_v2`.
5. Validar paridad de resultados y latencia (`p95`, `p99`, hit@k).
6. Retirar tabla antigua cuando la nueva este estable.

## Criterios para activar la migracion

- `content_chunks` supera umbral operativo definido (ej. > 30M rows).
- `p95` de retrieval supera objetivo de SLA.
- Evidencia de degradacion por ruido multi-tenant en indice vectorial.

## Notas

- Evitar migracion en caliente sin dual-write.
- Mantener rollback plan a tabla original hasta completar validaciones.
- Priorizar primero la RPC hibrida unificada (`retrieve_hybrid_optimized`) antes de particionar.
