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

## CLI por lotes

En la raiz del repo:

```bash
./ing.sh
```

Nota: `ing.sh` en raiz es un wrapper; la implementacion vive en `tools/ingestion-client/ing.sh`.

Flujo por defecto:

1. `POST /api/v1/ingestion/batches`
2. `POST /api/v1/ingestion/batches/{batch_id}/files` (uno por archivo)
3. poll de estado en `GET /api/v1/ingestion/batches/{batch_id}/status`

## Endpoints principales (producto)

Base URL local: `http://localhost:8000/api/v1`

- `POST /documents`: ingesta manual de archivo (`multipart/form-data`).
- `GET /documents`: lista documentos fuente.
- `GET /documents/{document_id}/status`: estado de documento.
- `DELETE /documents/{document_id}`: elimina documento.
- `POST /chat/completions`: retrieval-only (no genera texto final).
- `POST /chat/feedback`: feedback de respuesta.
- `GET /management/collections`: lista colecciones por tenant.
- `GET /management/queue/status`: estado de cola por tenant.
- `GET /management/health`: health de API v1.
- `GET /management/retrieval/metrics`: metricas runtime de uso/fallback del hybrid RPC.

Auth en entornos desplegados:

- Enviar `Authorization: Bearer <RAG_SERVICE_SECRET>` o `X-Service-Secret: <RAG_SERVICE_SECRET>`.
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

## Mapa de migracion (legacy -> v1)

- `POST /ingestion/ingest` -> `POST /documents`
- `GET /ingestion/documents` -> `GET /documents`
- `POST /knowledge/retrieve` -> `POST /chat/completions`
- `POST /retrieval/chunks` -> `POST /debug/retrieval/chunks`
- `POST /retrieval/summaries` -> `POST /debug/retrieval/summaries`

Las rutas legacy siguen disponibles durante la migracion para evitar ruptura de clientes.

Politica de deprecacion legacy:

- Headers en respuesta: `Deprecation: true` y `Sunset: Wed, 30 Sep 2026 00:00:00 GMT`.
- Endpoints legacy de retrieval crudo recomendados para debug: `/debug/retrieval/chunks` y `/debug/retrieval/summaries`.

## Comportamiento de scope en retrieval

- Si el sistema detecta alcance ambiguo, devuelve `requires_scope_clarification` y candidatos de scope en `/knowledge/retrieve`.
- Si el scope es valido, responde contexto grounded con `citations` y `mode`.

Ejemplo (aclaracion de scope en `/knowledge/retrieve`):

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
