# RAG Ingestion + Q/A Orchestrator

Motor de servicio de CISRE para ingesta cognitiva y retrieval estructurado con trazabilidad.

Disenado para operar como backend API-first en escenarios donde el naive RAG falla con tablas, figuras y dependencias entre documentos.

## Componentes principales

- `RAG backend`: ingestion, persistencia, retrieval hibrido y worker.
- `Q/A Orchestrator` (`app/qa_orchestrator`): capa de bibliotecario para planificar consulta, generar respuesta y validar evidencia.
- Contrato entre capas: `docs/qa-orchestrator-rag-boundary-contract.md`.

## Filosofia operativa

- **Ingesta Cognitiva (Visual Anchors)**: el pipeline visual parsea tablas/figuras a JSON estructurado. Aplica **Dual-Model Extraction** (Lite + Flash fallback).
- **Orquestación Tricameral**: enrutamiento dinámico nativo entre Vector Search, GraphRAG SQL-native y RAPTOR.
- **Stack unificado**: FastAPI + Supabase (Postgres 17 + pgvector), sin fragmentar en motores separados.

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
- `app/qa_orchestrator/`: orquestador de preguntas/respuestas (antes `app/mas_simple`).
- `tests/unit/`: pruebas unitarias.
- `tests/integration/`: pruebas de integracion del servicio.
- `tests/stress/`: pruebas de carga/robustez.
- `tests/evaluation/`: evaluaciones y benchmark de calidad.
- `scripts/`: utilidades de validacion/operacion.

## Documentacion adicional

- Canonica para todo el repo: `../docs/README.md`
- Arquitectura del servicio: `docs/architecture.md`
- Flujos y diagramas: `docs/flows-and-diagrams.md`
- Executive One-Page: `docs/one-page-architecture.md`
- ADRs: `docs/adr/README.md`
- Configuracion: `docs/configuration.md`
- Testing: `docs/testing.md`
- Runbooks: `docs/runbooks/common-incidents.md`
- Migration note (rename): `docs/migration-note-qa-orchestrator-rename.md`

## Dependency management

- Direct dependencies are tracked in `requirements.in`.
- Frozen runtime dependencies are pinned in `requirements.txt`.
- Automated update pipeline is configured via `.github/dependabot.yml`.
