# MAS Simple Engine (rag-ingestion)

Motor de servicio de CISRE para ingesta cognitiva y retrieval estructurado con trazabilidad.

Disenado para operar como backend API-first en escenarios donde el naive RAG falla con tablas, figuras y dependencias entre documentos.

## Filosofia operativa

- **Estructura como dato**: el pipeline visual parsea tablas/figuras a JSON y no solo texto plano.
- **Late Binding**: el contexto visual se hidrata cuando agrega valor, reduciendo costo y latencia.
- **Orquestacion Tricameral**: enrutamiento dinamico entre Vector, GraphRAG SQL-native y RAPTOR.
- **Stack unificado**: FastAPI + Supabase (Postgres + pgvector), sin fragmentar en motores separados.

## Quickstart

Desde la raiz del repo:

```bash
cd rag-ingestion
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

En otra terminal:

```bash
cd rag-ingestion
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

Flujo por defecto:

1. `POST /api/v1/ingestion/batches`
2. `POST /api/v1/ingestion/batches/{batch_id}/files` (uno por archivo)
3. poll de estado en `GET /api/v1/ingestion/batches/{batch_id}/status`

Fallback legacy (archivo por request):

```bash
./ing.sh --legacy
```

## Endpoints principales

Base URL local: `http://localhost:8000/api/v1`

- `POST /ingestion/embed`: embeddings de texto.
- `POST /ingestion/ingest`: ingesta manual de archivo (`multipart/form-data`).
- `POST /ingestion/institutional`: ingesta institucional protegida por `X-Service-Secret`.
- `GET /ingestion/documents`: lista documentos fuente.
- `POST /ingestion/retry/{doc_id}`: reintenta documento con estado fallido.
- `POST /ingestion/batches`: crea batch 2-step para N archivos.
- `POST /ingestion/batches/{batch_id}/files`: agrega archivo al batch.
- `POST /ingestion/batches/{batch_id}/seal`: endpoint disponible (opcional/legacy en flujo CLI actual).
- `GET /ingestion/batches/{batch_id}/status`: progreso del batch.
- `POST /knowledge/retrieve`: retrieval de contexto grounded.
- `POST /synthesis/generate`: crea job asyncrono de sintesis estructurada.
- `GET /synthesis/jobs/{job_id}`: estado del job de sintesis estructurada.
- `POST /curriculum/generate`: crea job asyncrono de sintesis estructurada (ruta legacy).
- `GET /curriculum/jobs/{job_id}`: estado del job de sintesis estructurada (ruta legacy).

## Estructura del modulo

- `app/`: codigo productivo (API, dominio, infraestructura y workflows).
- `tests/unit/`: pruebas unitarias.
- `tests/integration/`: pruebas de integracion del servicio.
- `tests/stress/`: pruebas de carga/robustez.
- `tests/evaluation/`: evaluaciones y benchmark de calidad.
- `scripts/`: utilidades de validacion/operacion.

## Documentacion adicional

- Canonica para todo el repo: `../docs/README.md`
- Arquitectura del servicio: `docs/architecture.md`
- Flujos y diagramas: `docs/flows-and-diagrams.md`
- ADRs: `docs/adr/README.md`
- Configuracion: `docs/configuration.md`
- Testing: `docs/testing.md`
- Runbooks: `docs/runbooks/common-incidents.md`

## Dependency management

- Direct dependencies are tracked in `requirements.in`.
- Frozen runtime dependencies are pinned in `requirements.txt`.
- Automated update pipeline is configured via `.github/dependabot.yml`.
