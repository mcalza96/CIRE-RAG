# Plan de migracion local: separar `rag-engine` y `qa-orchestrator`

Plan rapido para separar servicios en local, sin despliegue ni downtime.

## Objetivo

- `rag-engine`: API de conocimiento (ingestion, procesamiento, retrieval, worker).
- `qa-orchestrator`: API/control de preguntas (intencion, HITL, validacion, respuesta).

## Fase 1 (medio dia): frontera de contrato

Congelar contrato HTTP entre servicios (v1) con estos endpoints minimos en `rag-engine`:

- `POST /v1/retrieval/chunks`
- `POST /v1/retrieval/summaries`
- `POST /v1/ingestion/documents` (si se necesita orquestar ingesta desde `qa-orchestrator`)
- `GET /health`

Payload minimo requerido:

- `query`
- `tenant_id`
- `collection_id` (opcional)
- `plan` (modo, k, filtros)

Headers obligatorios:

- `X-Tenant-Id`
- `X-Correlation-Id`

## Fase 2 (medio dia): corte de codigo por ownership

### Se queda en `rag-engine`

- `app/api/v1/routers/ingestion.py`
- `app/services/ingestion/*`
- `app/application/use_cases/manual_ingestion_use_case.py`
- `app/application/use_cases/institutional_ingestion_use_case.py`
- `app/application/use_cases/process_document_worker_use_case.py`
- `app/worker.py`
- `run_worker.py`
- `app/services/retrieval/atomic_engine.py`
- `app/services/knowledge/knowledge_service.py`
- `app/services/knowledge/*` (excepto logica de respuesta final si se mueve al orquestador)
- `app/application/services/retrieval_broker.py`
- `app/application/services/retrieval_router.py`
- `app/application/services/query_decomposer.py`
- `app/infrastructure/*`
- `app/domain/interfaces/retrieval_interface.py`
- `app/domain/interfaces/embedding_provider.py`
- `app/services/embedding_service.py`

### Se mueve a `qa-orchestrator`

- `orchestrator/runtime/qa_orchestrator/*`
- `orchestrator/runtime/orchestrator_api/*`
- `orchestrator/chat_cli.py` (CLI split por HTTP)
- Endpoint de answer/HITL (actualmente en `app/api/v1/routers/knowledge.py`)

### Ficheros a dividir (boundary)

- `app/api/v1/routers/knowledge.py`
  - `retrieve` pasa a `rag-engine`.
  - `answer` y `scope-health` pasan a `qa-orchestrator`.

## Fase 3 (medio dia): cliente HTTP del orquestador

Crear cliente `RagEngineClient` en `qa-orchestrator` con `httpx`:

- timeout configurable (ej. 8s)
- retry corto con backoff (2 intentos)
- propagacion de `X-Correlation-Id`

El `HandleQuestionUseCase` deja de depender de retrieval local y consume `rag-engine` por HTTP.

## Fase 4 (2-3 horas): puertos y ejecucion local

- `rag-engine`: `:8000`
- `qa-orchestrator`: `:8001`

Variables recomendadas en `qa-orchestrator`:

- `RAG_ENGINE_URL=http://localhost:8000`
- `ORCH_PORT=8001`
- `ORCH_USE_REMOTE_RAG=true`

Mantener `stack.sh` para levantar ambos procesos (sin docker obligatorio).

## Fase 5 (2-3 horas): pruebas E2E locales

Casos minimos:

1. ingestion -> worker -> retrieve (rag-engine)
2. question -> HITL clarification -> answer (qa-orchestrator)
3. scope mismatch -> bloqueo controlado (qa-orchestrator)

Checks:

- `curl :8000/health` y `curl :8001/health`
- trazabilidad por `X-Correlation-Id`
- paridad funcional con flujo actual CLI

Comandos de smoke test rapido:

```bash
./stack.sh up
curl -s http://localhost:8000/health
curl -s http://localhost:8001/health

curl -s -X POST "http://localhost:8000/api/v1/retrieval/chunks" \
  -H "Content-Type: application/json" \
  -d '{"query":"Que exige ISO 9001 sobre informacion documentada?","tenant_id":"<TENANT_ID>","chunk_k":8,"fetch_k":40}'

curl -s -X POST "http://localhost:8001/api/v1/knowledge/answer" \
  -H "Content-Type: application/json" \
  -d '{"query":"Que exige ISO 9001 sobre informacion documentada?","tenant_id":"<TENANT_ID>"}'
```

## Ajustes de scripts en este repo

- `stack.sh`: agregar proceso `qa-orchestrator` en `:8001` (ya reserva puerto 8001).
- `dev.sh`: incluir log dedicado del orquestador.
- `chat.sh`: apuntar a `qa-orchestrator` para responder y a `rag-engine` solo para operaciones de soporte.
- `ing.sh`: mantenerlo apuntando a `rag-engine`.

Estado actual implementado:

- `chat.sh` ya usa `orchestrator/chat_cli.py` contra `ORCH_URL` (default `http://localhost:8001`).
- `ing.sh` se mantiene sobre `RAG_URL` (default `http://localhost:8000`).
- Se creo carpeta de staging `orchestrator/` con wrappers de runtime para preparar extraccion a repo independiente.
- Se movio runtime de orquestacion a `orchestrator/runtime/qa_orchestrator` y `orchestrator/runtime/orchestrator_api`; `app/*` queda como shim temporal.

## Definicion de listo (DoD)

- No hay imports cruzados de runtime entre `rag-engine` y `qa-orchestrator`.
- `qa-orchestrator` solo habla con `rag-engine` por HTTP.
- Flujo completo local pasa en ambos modos: normal y HITL.
- Documentacion actualizada en:
  - `docs/architecture.md`
  - `docs/operations.md`
  - `docs/architecture.md`

## Orden recomendado de ejecucion (express)

1. partir `knowledge` endpoint (retrieve vs answer)
2. crear `RagEngineClient` y cablear `HandleQuestionUseCase`
3. levantar ambos servicios en local
4. correr pruebas E2E
5. limpiar imports cruzados
