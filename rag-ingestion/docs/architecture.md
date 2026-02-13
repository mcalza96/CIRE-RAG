# Arquitectura de rag-ingestion

## Vision general

`rag-ingestion` separa responsabilidades en capas para reducir acoplamiento y permitir evolucion por partes:

- API y contratos HTTP en `app/api`.
- Casos de uso en `app/application`.
- Reglas de negocio e interfaces en `app/domain`.
- Integraciones externas en `app/infrastructure`.
- Logica de ingestion/retrieval en `app/services`: incluye `AtomicRetrievalEngine` por defecto.
- Orquestacion de procesos en `app/workflows`.

## Flujo 1: ingesta manual

1. Cliente llama `POST /api/v1/ingestion/ingest`.
2. `ManualIngestionUseCase` valida metadata y persiste registro inicial.
3. Se agenda background task para procesamiento.
4. Worker y/o pipeline de ingestion ejecuta parsing, chunking, embeddings y persistencia.
5. El documento queda disponible para retrieval y trazabilidad.

## Flujo 2: ingesta institucional

1. Servicio externo llama `POST /api/v1/ingestion/institutional`.
2. Se valida `X-Service-Secret` contra `RAG_SERVICE_SECRET`.
3. `InstitutionalIngestionUseCase` crea/actualiza contexto institucional.
4. El procesamiento se delega al pipeline y repositorios de infraestructura.

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
- Suscripcion Realtime a tabla `source_documents` (INSERT y UPDATE para retry).
- Control de concurrencia por `doc_id` en memoria para evitar reprocesos simultaneos.

## Persistencia e integraciones

- Supabase client en `app/infrastructure/supabase/client.py`.
- Repositorios Supabase en `app/infrastructure/repositories`.
- Esquema Consolidado: Se mantiene un núcleo de **34 tablas core** optimizadas, habiendo purgado todo el legado de "TeacherOS".
- Migraciones SQL en `app/infrastructure/migrations`.

## Coexistencia Q/A Orchestrator y RAG

`app/qa_orchestrator` coexiste como capa de orquestacion de Q/A (bibliotecario),
mientras RAG mantiene el conocimiento y retrieval (biblioteca).

Nota de migracion de naming: el modulo historico `app/mas_simple` fue renombrado a
`app/qa_orchestrator` para evitar confusion con "MAS" (Multi-Agent Systems).

Contrato de frontera: `docs/qa-orchestrator-rag-boundary-contract.md`.

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

Checklist rapido para PRs:

1. Esta abstraccion reduce acoplamiento real o solo agrega wiring?
2. Existe al menos una segunda implementacion plausible y cercana?
3. Que test o escenario quedaria mas dificil sin este contrato?
4. El costo cognitivo (archivos/imports/saltos) esta justificado?
