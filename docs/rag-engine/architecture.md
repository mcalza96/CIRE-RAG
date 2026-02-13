# Arquitectura de rag-engine

## Vision general

`rag-engine` separa responsabilidades en capas para reducir acoplamiento y permitir evolucion por partes:

- API y contratos HTTP en `app/api`.
- Casos de uso en `app/application`.
- Reglas de negocio y tipos en `app/domain`.
- Integraciones externas en `app/infrastructure`.
- Logica de ingestion/retrieval en `app/services`: incluye `AtomicRetrievalEngine` por defecto.
- Orquestacion de procesos en `app/workflows`.

## Flujo E2E actual (ingestion -> proceso -> pregunta -> analisis -> respuesta)

1. **Ingestion**: `POST /api/v1/ingestion/ingest` o `POST /api/v1/ingestion/institutional` registran `source_documents` en estado `queued`, con metadatos de tenant/coleccion/estrategia y snapshot de cola.
2. **Proceso**: el worker (`app/worker.py`) opera en modo pull sobre `job_queue` (`fetch_next_job`) y ejecuta `ProcessDocumentWorkerUseCase` con control de concurrencia global y por tenant.
3. **Pregunta**: el cliente consulta retrieval en rag-engine (`/knowledge/retrieve`, `/retrieval/*`) y respuesta en orquestador (`http://localhost:8001/api/v1/knowledge/answer`) o por `orchestrator/chat_cli.py`.
4. **Analisis**: el orquestador clasifica intencion, arma `RetrievalPlan`, puede pedir aclaracion de alcance (p. ej. multinorma/conflicto), recupera evidencia C#/R#, genera borrador y valida scope/evidencia literal.
5. **Respuesta**: se retorna respuesta grounded con trazabilidad o se bloquea/solicita aclaracion cuando hay inconsistencia de ambito.

## Flujo 1: ingesta manual

1. Cliente llama `POST /api/v1/ingestion/ingest`.
2. `ManualIngestionUseCase` valida metadata, aplica backpressure por tenant y registra `source_documents` en `queued`.
3. Se devuelve `accepted` con snapshot de cola (`queue_depth`, `estimated_wait_seconds`, `max_pending`).
4. El worker toma el job en modo pull y ejecuta parsing/chunking/embeddings/persistencia/etapas opcionales.
5. El documento queda disponible para retrieval y trazabilidad.

## Flujo 2: ingesta institucional

1. Servicio externo llama `POST /api/v1/ingestion/institutional`.
2. Se valida `X-Service-Secret` contra `RAG_SERVICE_SECRET`.
3. `InstitutionalIngestionUseCase` crea/actualiza contexto institucional y registra estrategia (`CONTENT` o `PRE_PROCESSED`).
4. El documento queda en `queued`; el worker lo procesa por `job_queue` con el mismo pipeline de manual ingestion.

## Flujo 3: retrieval

1. Cliente llama `POST /api/v1/knowledge/retrieve` con `tenant_id` y query.
2. `KnowledgeService` coordina la estrategia de retrieval delegando en el `AtomicRetrievalEngine` (enrutado tricameral).
3. Se retorna contexto con chunks relevantes, visual anchors y trazabilidad de modo.

## API y middleware

- Entrada principal en `app/main.py`.
- Routers versionados en `app/api/v1`.
- Middleware de correlacion en `app/core/observability/correlation.py`.
- Middleware de contexto de negocio en `app/core/middleware/business_context.py`.

## Worker y procesamiento reactivo

- Entrypoint de worker: `run_worker.py`.
- Implementacion principal: `app/worker.py`.
- Modelo pull sobre `job_queue` mediante RPC `fetch_next_job`.
- Pollers concurrentes con limites globales y por tenant (`WORKER_CONCURRENCY`, `WORKER_PER_TENANT_CONCURRENCY`).
- Control de concurrencia por `doc_id` en memoria para evitar reprocesos simultaneos.
- Metricas de saturacion por tenant (active_jobs, queue_depth, queue_wait_seconds) y alertas de backlog.

## Persistencia e integraciones

- Supabase client en `app/infrastructure/supabase/client.py`.
- Repositorios Supabase en `app/infrastructure/repositories`.
- Esquema Consolidado: Se mantiene un núcleo de **34 tablas core** optimizadas, habiendo purgado todo el legado de "TeacherOS".
- Migraciones SQL en `app/infrastructure/migrations`.

## Coexistencia Q/A Orchestrator y RAG

`orchestrator/runtime/qa_orchestrator` coexiste como capa de orquestacion de Q/A (bibliotecario),
mientras RAG mantiene el conocimiento y retrieval (biblioteca).

En la implementacion actual, `HandleQuestionUseCase` se cablea con clases concretas
de `orchestrator/runtime/qa_orchestrator/adapters.py` para reducir complejidad de wiring.

Nota de migracion de naming: el modulo historico `app/mas_simple` fue renombrado a
`qa_orchestrator` para evitar confusion con "MAS" (Multi-Agent Systems).

Notas de integracion: `docs/qa-rag-integration-notes.md`.

## Observabilidad

- Logs estructurados con `structlog`.
- Correlation ID propagado por middleware.
- Hooks de metricas/forensics en `app/core/observability`.

## Guia de contratos (interfaces y Protocol)

Usar contratos solo cuando bajan complejidad total. Regla general: en Python priorizar simplicidad y duck typing.

- Crear contrato nuevo solo si hay 2+ implementaciones reales o un cambio de proveedor esperado en el corto plazo.
- Mantener fronteras explicitas para dependencias externas caras: DB, LLM, embeddings, red.
- Para colaboraciones internas con una sola implementacion, preferir clase concreta y evitar archivos de interfaz 1:1.
- Preferir `Protocol` sobre `ABC` cuando solo se necesita contrato de tipos (sin comportamiento compartido).
- Si una interfaz no tiene consumidores activos o no aporta seam de test real, eliminarla.

Estado actual (simplificado):

- `IRetrievalRepository` (`Protocol`) para frontera de retrieval.
- `IEmbeddingProvider` (`ABC`) para switch cloud/local real.
- Resto de servicios internos usan clases concretas para reducir wiring.

Checklist rapido para PRs:

1. Esta abstraccion reduce acoplamiento real o solo agrega wiring?
2. Existe al menos una segunda implementacion plausible y cercana?
3. Que test o escenario quedaria mas dificil sin este contrato?
4. El costo cognitivo (archivos/imports/saltos) esta justificado?
