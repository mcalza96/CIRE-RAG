# CIRE-RAG

Engine API-first para ingestion cognitiva y retrieval estructurado con trazabilidad.

## Que es

- Backend de RAG de alta precision para documentos densos (tablas, figuras, dependencias entre fuentes).
- Diseñado para flujos auditables: evidencia antes que fluidez.
- Stack: FastAPI + Supabase (Postgres/pgvector) + worker pull sobre `job_queue`.

## Inicio rapido

```bash
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

En otra terminal:

```bash
venv/bin/python run_worker.py
```

Health:

```bash
curl http://localhost:8000/health
```

## Despliegue (API y Worker separados)

El repositorio usa un Dockerfile multi-stage con tres targets:

- `api_image`: servicio HTTP (`start_api.sh`, puerto `${PORT:-8000}`).
- `worker_cloud_image`: worker liviano (core-only), recomendado para Railway/Render.
- `worker_image`: worker pesado (incluye `requirements-local.txt`, ej. torch/transformers).

Build examples:

```bash
docker build --target api_image -t cire-rag-api .
docker build --target worker_cloud_image -t cire-rag-worker-cloud .
docker build --target worker_image -t cire-rag-worker .
```

Ejecucion con Docker Compose:

```bash
cp .env.example .env.local
docker compose up --build -d api worker
```

Worker pesado local (opcional):

```bash
docker compose --profile local-heavy up --build -d worker-local
```

Recomendacion de costo cloud:

- API: `api_image`
- Worker: `worker_cloud_image`
- Variables: `JINA_MODE=CLOUD`, `JINA_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

## Documentación central

- **[E2E Flow & Security](docs/e2e.md)**: Guía sobre aislamiento de datos, LeakCanary y flujo de retrieval.
- **[Documentation Hub](docs/README.md)**: Punto de entrada para guías de desarrollo e infraestructura.
- **SDKs**: Referencias para [TypeScript](sdk/ts/README.md) y [Python](sdk/python/README.md).

## API v1 (contrato recomendado)

Base URL local: `http://localhost:8000/api/v1`

- `POST /chat/completions`: retrieval grounded para orquestadores (`context_chunks`, `citations`, `mode`, scope clarification).
- `POST /chat/feedback`: recepcion de feedback de interaccion.
- `POST /documents`: subir documento y encolarlo (idempotencia + backpressure).
- `GET /documents`: listar documentos del tenant.
- `GET /documents/{document_id}/status`: estado de ingesta por documento.
- `DELETE /documents/{document_id}`: borrar documento y opcionalmente chunks.
- `GET /management/tenants`: catalogo de tenants disponibles para caller S2S.
- `GET /management/collections`: colecciones por tenant.
- `GET /management/queue/status`: profundidad/ETA de cola por tenant.
- `GET /management/health`: health del dominio management.
- `GET /management/retrieval/metrics`: metricas runtime de retrieval hibrido + estado de contrato RPC (`rpc_contract_status`, `rpc_contract_mismatch_events`).
- `POST /debug/retrieval/chunks`: retrieval de chunks para diagnostico/control fino.
- `POST /debug/retrieval/summaries`: retrieval de summaries para diagnostico/control fino.
- `POST /ingestion/batches`, `POST /ingestion/batches/{batch_id}/files`, `GET /ingestion/batches/{batch_id}/status|progress|events|stream`, `GET /ingestion/batches/active`: flujo operativo de batch ingestion/observabilidad.

Auth (entornos desplegados, por ejemplo `APP_ENV=production`): enviar `Authorization: Bearer <RAG_SERVICE_SECRET>` o `X-Service-Secret: <RAG_SERVICE_SECRET>`.
Contexto multitenant S2S: enviar siempre `X-Tenant-ID` (requerido en todas las rutas S2S excepto `/health`, `/docs`, `/openapi.json`). Si el payload/query incluye `tenant_id`, debe coincidir con `X-Tenant-ID`.
Cobertura S2S: rutas de negocio en `/chat`, `/documents`, `/management`, `/debug/retrieval` y `/ingestion` requieren auth en entorno desplegado (incluyendo `/ingestion/embed`).

Idempotencia en ingesta: `POST /documents` acepta header `Idempotency-Key`; reintentos con la misma llave retornan la misma respuesta (persistida en Redis cuando esta disponible, fallback en memoria).

Endpoints retirados (ya no montados): `POST /retrieval/chunks`, `POST /retrieval/summaries`, `POST /knowledge/retrieve`.

## Estructura

- `app/`: API, dominio, servicios, infraestructura y workflows.
- `tests/`: unit, integration, stress, evaluation.
- `scripts/`: utilidades operativas.
- `supabase/`: assets SQL/migrations.

## Calidad

```bash
ruff check app tests scripts run_worker.py
mypy --config-file mypy.ini -m app.domain.schemas.ingestion_schemas -m app.core.config.model_config -m app.services.retrieval.atomic_engine -m app.services.ingestion.visual_parser
pytest tests/unit tests/integration tests/tools -q
```
