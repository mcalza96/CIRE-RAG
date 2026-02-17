# RAG Engine

Motor de servicio de CIRE-RAG para ingesta cognitiva y retrieval estructurado con trazabilidad.

Disenado para operar como backend API-first en escenarios donde el naive RAG falla con tablas, figuras y dependencias entre documentos.

## Componentes principales

- `RAG backend`: ingestion, persistencia, retrieval hibrido y worker.
- Clientes externos (incluyendo orquestadores) consumen retrieval via HTTP.

## Filosofia operativa

- **Ingesta Cognitiva (Visual Anchors)**: el pipeline visual parsea tablas/figuras a JSON estructurado. Aplica **Dual-Model Extraction** (Lite + Flash fallback).
- **Retrieval Atómico (Atomic Engine)**: orquestación dinámica mediante `QueryDecomposer` que combina Vector Search, Full-Text Search (FTS) y navegación de grafos multi-hop en una única fase de búsqueda atómica.
- **RAPTOR (cuando aplica)**: construye un arbol jerarquico de resumenes mediante clustering semantico recursivo (no depende de estructura fija pagina/capitulo).
- **Stack unificado**: FastAPI + Supabase (Postgres 17 + pgvector), sin fragmentar en motores separados.

## Flujo operativo actual

1. **Ingestion**: endpoints de ingesta registran documento en `queued` y devuelven snapshot de cola.
2. **Proceso**: `run_worker.py` consume `job_queue` en modo pull (`fetch_next_job`) y ejecuta pipeline de parseo/chunking/embeddings/persistencia, con Visual Anchors + RAPTOR + Graph opcionales.
3. **Pregunta**: clientes consumen endpoint retrieval-only en `/chat/completions`.
4. **Analisis**: el engine aplica descomposicion de consulta, fusion hibrida y filtros de scope.
5. **Respuesta**: retorna evidencia (`context_chunks`, `context_map`, `citations`, `mode`) para que el orquestador genere la respuesta final.

## Quickstart

Desde la raiz del repo:

```bash
cd .
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

Para desarrollo con embeddings locales (torch/transformers):

```bash
INSTALL_LOCAL_EMBEDDINGS=1 JINA_MODE=LOCAL ./bootstrap.sh
```

En otra terminal:

```bash
cd .
venv/bin/python run_worker.py
```

Health check:

```bash
curl http://localhost:8000/health
```

## Ingestión por lotes (API)

Flujo por defecto:

1. `POST /api/v1/ingestion/batches`
2. `POST /api/v1/ingestion/batches/{batch_id}/files` (uno por archivo)
3. poll de estado en `GET /api/v1/ingestion/batches/{batch_id}/status`

## Endpoints principales (producto)

Base URL local: `http://localhost:8000/api/v1`

- `POST /chat/completions`: retrieval grounded para orquestadores (no genera texto final).
- `POST /chat/feedback`: feedback de respuesta.
- `POST /documents`: ingesta manual de archivo (`multipart/form-data`).
- `GET /documents`: lista documentos fuente.
- `GET /documents/{document_id}/status`: estado de documento.
- `DELETE /documents/{document_id}`: elimina documento.
- `GET /management/tenants`: lista tenants disponibles para caller S2S.
- `GET /management/collections`: lista colecciones por tenant.
- `GET /management/queue/status`: estado de cola por tenant.
- `GET /management/health`: health de API v1.
- `GET /management/retrieval/metrics`: metricas runtime de uso/fallback del hybrid RPC + estado de contrato (`rpc_contract_status`, `rpc_contract_mismatch_events`).
- `POST /retrieval/hybrid`: retrieval unificado (vector + FTS + graph) con filtros de scope.
- `POST /retrieval/multi-query`: ejecucion de subconsultas y fusion RRF global con modo fail-soft parcial.
- `POST /retrieval/explain`: retrieval con explicacion segura de score/path/filtros aplicados.
- `POST /retrieval/validate-scope`: prevalidacion strict-deny de filtros/scope antes de ejecutar retrieval.
- `POST /debug/retrieval/chunks`: retrieval de chunks para diagnostico/control fino.
- `POST /debug/retrieval/summaries`: retrieval de summaries para diagnostico/control fino.
- `POST /ingestion/batches`: crear batch de ingesta.
- `POST /ingestion/batches/{batch_id}/files`: subir archivos al batch.
- `GET /ingestion/batches/{batch_id}/status`: estado del batch.
- `GET /ingestion/batches/{batch_id}/progress`: progreso proyectado por etapa.
- `GET /ingestion/batches/{batch_id}/events`: timeline paginado de eventos.
- `GET /ingestion/batches/{batch_id}/stream`: stream SSE de progreso/eventos.
- `GET /ingestion/batches/active`: batches activos por tenant.

Auth en entornos desplegados:

- Enviar `Authorization: Bearer <RAG_SERVICE_SECRET>` o `X-Service-Secret: <RAG_SERVICE_SECRET>`.
- Enviar `X-Tenant-ID` en todas las rutas S2S (excepto `/health`, `/docs`, `/openapi.json`).
- Si el request trae `tenant_id` en query/body, debe coincidir con `X-Tenant-ID`.
- Rutas protegidas S2S: `/chat`, `/documents`, `/management`, `/retrieval`, `/debug/retrieval`, `/ingestion` (incluye `/ingestion/embed`).
- En entorno local (`APP_ENV=local`) los endpoints permiten desarrollo sin token.

Idempotencia en ingesta:

- `POST /documents` acepta header `Idempotency-Key`.
- Reintentos con la misma llave devuelven la misma respuesta (`X-Idempotency-Replayed: true`).
- El backend usa Redis cuando esta disponible; fallback en memoria cuando Redis no responde.

Contexto conversacional en chat:

- `POST /chat/completions` acepta `history` y lo usa para enriquecer retrieval multi-turn sin perder contexto referencial.

## Optimizacion SQL (super-query)

- El motor atomico puede usar RPC unificada `retrieve_hybrid_optimized` para ejecutar vector + FTS + RRF en una sola llamada SQL.
- Control por flag `ATOMIC_USE_HYBRID_RPC=true` (fallback automatico a primitives si la RPC falla).
- Startup preflight valida firma efectiva de la RPC; si detecta mismatch de `hnsw_ef_search`, autodegrada a primitives en runtime y expone `HYBRID_RPC_SIGNATURE_MISMATCH_HNSW` en trace.
- Ajuste fino HNSW por consulta: `ATOMIC_HNSW_EF_SEARCH`.
- Control de latencia de reranking: `RERANK_MAX_CANDIDATES`.

## Retrieval avanzado: ejemplos `curl`

`POST /api/v1/retrieval/hybrid`

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/hybrid" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{
    "query":"Que exige ISO 9001 sobre control documental?",
    "tenant_id":"<TENANT_ID>",
    "k":12,
    "fetch_k":60,
    "filters":{
      "source_standard":"ISO 9001",
      "time_range":{"field":"updated_at","from":"2024-01-01T00:00:00Z"}
    }
  }'
```

`POST /api/v1/retrieval/multi-query`

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/multi-query" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{
    "tenant_id":"<TENANT_ID>",
    "queries":[
      {"id":"q1","query":"control documental ISO 9001"},
      {"id":"q2","query":"retencion de registros ISO 9001"}
    ],
    "merge":{"strategy":"rrf","rrf_k":60,"top_k":12}
  }'
```

`POST /api/v1/retrieval/explain`

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/explain" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{
    "query":"Que exige ISO 9001 7.5.3?",
    "tenant_id":"<TENANT_ID>",
    "top_n":5
  }'
```

`POST /api/v1/retrieval/validate-scope`

```bash
curl -s -X POST "http://localhost:8000/api/v1/retrieval/validate-scope" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: <TENANT_ID>" \
  -d '{
    "query":"Que exige ISO 9001 7.5.3?",
    "tenant_id":"<TENANT_ID>",
    "filters":{"source_standard":"ISO 9001"}
  }'
```

## Que usar desde ORCH

- Hoy: `POST /api/v1/debug/retrieval/chunks` y `POST /api/v1/debug/retrieval/summaries` siguen disponibles para diagnostico/control fino.
- Nuevo contrato oficial: `POST /api/v1/retrieval/hybrid` y `POST /api/v1/retrieval/multi-query` para adopcion incremental via feature flag en ORCH.
- `POST /api/v1/retrieval/explain` y `POST /api/v1/retrieval/validate-scope` son utilitarios de auditoria/seguridad para pipelines industriales.

## Endpoints retirados

- `POST /knowledge/retrieve`
- `POST /retrieval/chunks` (legacy)
- `POST /retrieval/summaries` (legacy)

## Comportamiento de scope en retrieval

- Si el sistema detecta alcance ambiguo, devuelve `requires_scope_clarification` en `/chat/completions`.
- Si el scope es valido, responde contexto grounded con `citations` y `mode`.

Ejemplo (aclaracion de scope en `/chat/completions`):

```json
{
  "context_chunks": [],
  "context_map": {},
  "citations": [],
  "mode": "AMBIGUOUS_SCOPE",
  "scope_candidates": ["ISO 9001", "ISO 14001", "ISO 45001"],
  "scope_message": "Necesito desambiguar el alcance antes de responder..."
}
```

## Estructura del modulo

- `app/`: codigo productivo (API, dominio, infraestructura y workflows).
- `tests/unit/`: pruebas unitarias.
- `tests/integration/`: pruebas de integracion del servicio.
- `tests/stress/`: pruebas de carga/robustez.
- `tests/evaluation/`: evaluaciones y benchmark de calidad.
- `scripts/`: utilidades de validacion/operacion.

## Documentacion adicional

- Canonica para todo el repo: `../README.md`
- Arquitectura del servicio: `architecture.md`
- Flujos y diagramas: `flows-and-diagrams.md`
- Executive One-Page: `one-page-architecture.md`
- ADRs: `adr/README.md`
- Configuracion: `configuration.md`
- Testing: `testing.md`
- Runbooks: `runbooks/common-incidents.md`
- Plan de particionamiento por tenant: `runbooks/tenant-partitioning-plan.md`
- Baseline Visual Anchors: `runbooks/visual-anchor-baseline.md`
- Costos cloud ON/OFF: `runbooks/cloud-cost-on-off.md`

## Dependency management

- Direct dependencies are tracked in `requirements.in`.
- Frozen runtime dependencies are pinned in `requirements.txt`.
- Runtime install split:
  - `requirements-core.txt`: cloud/API baseline (no torch/transformers).
  - `requirements-local.txt`: local embedding runtime extras.
- Automated update pipeline is configured via `.github/dependabot.yml`.
