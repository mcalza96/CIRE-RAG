# Arquitectura de rag-ingestion

## Vision general

`rag-ingestion` separa responsabilidades en capas para reducir acoplamiento y permitir evolucion por partes:

- API y contratos HTTP en `app/api`.
- Casos de uso en `app/application`.
- Reglas de negocio e interfaces en `app/domain`.
- Integraciones externas en `app/infrastructure`.
- Logica de ingestion/retrieval en `app/services`.
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
2. `KnowledgeService` coordina estrategia de retrieval y grounding.
3. Se retorna contexto con chunks relevantes para consumo de capas superiores.

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
- Migraciones SQL en `app/infrastructure/migrations` y `app/infrastructure/database/migrations`.

## Observabilidad

- Logs estructurados con `structlog`.
- Correlation ID propagado por middleware.
- Hooks de metricas/forensics en `app/core/observability`.
