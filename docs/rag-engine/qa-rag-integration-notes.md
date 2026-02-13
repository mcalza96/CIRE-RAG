# Q/A Orchestrator + RAG Integration Notes

Documento corto para coordinar modulos dentro del mismo repo.
No es un contrato formal ni versionado legal.

## Regla simple

- `orchestrator/runtime/qa_orchestrator` decide plan, validacion y formato de respuesta.
- El backend RAG ejecuta retrieval/persistencia y devuelve evidencia.

## Punto de integracion recomendado

- Q/A usa adaptadores en `orchestrator/runtime/qa_orchestrator/adapters.py`.
- Para retrieval reutiliza servicios existentes del backend (sin SQL inline en el orquestador).

## Datos esperados (practico)

- Entrada minima a retrieval: `query`, `tenant_id`, `collection_id?`, `plan`.
- Salida minima: lista de evidencia con `source`, `content`, `score`, `metadata.row`.

## Fallas y comportamiento

- Si retrieval falla o timeout: responder fallback controlado (sin stacktrace).
- Si no hay evidencia: explicitar limite de contexto.
- Si hay conflicto de scope: aclaracion HITL o bloqueo controlado segun politicas.

## Principio de mantenimiento

- Preferir cambios de integracion guiados por tipos/tests/linter.
- Evitar duplicar reglas en docs largas cuando el codigo ya expresa la verdad.
