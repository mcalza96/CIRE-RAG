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

## Documentacion central

- Hub general: `docs/README.md`
- Arquitectura del engine: `docs/rag-engine/architecture.md`
- Endpoints y flujo operativo: `docs/rag-engine/README.md`
- Configuracion: `docs/rag-engine/configuration.md`
- Runbooks: `docs/rag-engine/runbooks/common-incidents.md`
- SDK base TypeScript: `sdk/ts/README.md`
- SDK base Python: `sdk/python/README.md`

## API v1 (contrato recomendado)

Base URL local: `http://localhost:8000/api/v1`

- `POST /documents`: sube documento y devuelve `document_id` con estado `accepted`.
- `GET /documents/{document_id}/status`: estado de procesamiento (`queued|processing|completed|failed`).
- `DELETE /documents/{document_id}`: elimina documento (opcionalmente chunks).
- `POST /chat/completions`: retrieval-only (`context_chunks`, `context_map`, `citations`) para orquestadores.
- `POST /chat/feedback`: feedback de la respuesta.
- `GET /management/collections`: colecciones por tenant.
- `GET /management/queue/status`: profundidad y ETA de cola.
- `GET /management/health`: health de API v1.
- `GET /management/retrieval/metrics`: metricas runtime del backend de retrieval.

Auth (entornos desplegados, por ejemplo `APP_ENV=production`): enviar `Authorization: Bearer <RAG_SERVICE_SECRET>` o `X-Service-Secret: <RAG_SERVICE_SECRET>`.
Contexto multitenant S2S: enviar siempre `X-Tenant-ID` (requerido en todas las rutas S2S excepto `/health`, `/docs`, `/openapi.json`). Si el payload/query incluye `tenant_id`, debe coincidir con `X-Tenant-ID`.
Cobertura S2S: rutas de negocio en `/chat`, `/documents`, `/management`, `/retrieval`, `/knowledge` y `/ingestion` requieren auth en entorno desplegado (incluyendo `/ingestion/embed`).

Idempotencia en ingesta: `POST /documents` acepta header `Idempotency-Key`; reintentos con la misma llave retornan la misma respuesta (persistida en Redis cuando esta disponible, fallback en memoria).

Rutas legacy siguen activas temporalmente en `/ingestion`, `/knowledge` y `/retrieval`.
Estas rutas incluyen headers de deprecacion (`Deprecation: true`, `Sunset: Wed, 30 Sep 2026 00:00:00 GMT`).

## Estructura

- `app/`: API, dominio, servicios, infraestructura y workflows.
- `tests/`: unit, integration, stress, evaluation.
- `scripts/`: utilidades operativas.
- `supabase/`: assets SQL/migrations.

## Calidad

```bash
ruff check app tests scripts run_worker.py
mypy --config-file mypy.ini -m app.schemas.ingestion -m app.core.config.model_config -m app.services.retrieval.atomic_engine -m app.services.ingestion.visual_parser
pytest tests/unit tests/integration tests/tools -q
```
