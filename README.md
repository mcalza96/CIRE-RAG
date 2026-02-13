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

El repositorio usa un Dockerfile multi-stage con dos targets:

- `api_image`: servicio HTTP (`start_api.sh`, puerto `8000`).
- `worker_image`: procesamiento async (`start_worker.sh`).

Build examples:

```bash
docker build --target api_image -t cire-rag-api .
docker build --target worker_image -t cire-rag-worker .
```

## Documentacion central

- Hub general: `docs/README.md`
- Arquitectura del engine: `docs/rag-engine/architecture.md`
- Endpoints y flujo operativo: `docs/rag-engine/README.md`
- Configuracion: `docs/rag-engine/configuration.md`
- Runbooks: `docs/rag-engine/runbooks/common-incidents.md`

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
