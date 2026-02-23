# Architecture Boundaries

Esta guía define límites de dependencia para mantener la separación entre búsqueda (motor) y generación, con contratos claros entre capas.

## Objetivo

- Evitar que la lógica de dominio quede acoplada a proveedores concretos (DB, SDKs, modelos).
- Facilitar refactors por capas sin romper runtime.
- Mantener trazabilidad de deuda técnica mediante excepciones explícitas.

## Regla de Dependencias

Dirección permitida (de arriba hacia abajo):

`api -> workflows -> domain -> infrastructure`

Notas:

- `domain` define reglas, tipos y puertos.
- `infrastructure` implementa puertos y acceso a sistemas externos.
- `workflows` orquesta casos de uso y coordina domain + infrastructure.
- `api` expone contratos HTTP y adapta requests/responses.

## Reglas Prácticas

- `app/domain/**` no debe importar `app.infrastructure.*`.
- `app/domain/**` no debe importar `app.ai.*` directamente; usar puertos o servicios de aplicación.
- Si hay una excepción temporal, debe quedar en allowlist de tests con plan de remoción.

## Guardrails en Tests

Archivo: `tests/unit/test_architecture_supabase_boundary.py`

Valida que:

- No aparezcan nuevos imports `app.domain -> app.infrastructure`.
- No aparezcan nuevos imports `app.domain -> app.ai`.

El test usa allowlists de baseline para no romper runtime actual mientras se migra por fases.

## Plan de Refactor por Fases

Fase 1 (corta, sin riesgo):

- Congelar deuda con allowlists (ya activo en tests).
- Corregir documentación para reflejar rutas reales.

Fase 2 (ingestion):

- Mover dependencias de `app/domain/ingestion/**` a puertos.
- Implementar adapters en `app/infrastructure/**`.
- Inyectar dependencias desde `app/infrastructure/container.py`.

Fase 3 (retrieval):

- Extraer `settings`, clientes y repositorios fuera de `app/domain/retrieval/**`.
- Mantener en domain solo políticas, estrategias puras y validación.

Fase 4 (ai contracts):

- Encapsular llamadas a embeddings/rerank/llm en application/workflow services.
- Dejar en domain solo interfaces y DTOs estables.

## Criterio de Cierre

- Allowlists vacías en `tests/unit/test_architecture_supabase_boundary.py`.
- Sin imports cruzados prohibidos en `app/domain/**`.
- Documentación y quality gates alineados con estructura final.
