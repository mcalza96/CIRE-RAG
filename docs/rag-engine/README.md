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
3. **Pregunta**: clientes consultan retrieval por `/knowledge/retrieve` y `/retrieval/*`.
4. **Analisis**: el engine aplica descomposicion de consulta, fusion hibrida y filtros de scope.
5. **Respuesta**: retorna contexto grounded y trazabilidad para consumo de aplicaciones cliente.

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

Flujo por defecto:

1. `POST /api/v1/ingestion/batches`
2. `POST /api/v1/ingestion/batches/{batch_id}/files` (uno por archivo)
3. poll de estado en `GET /api/v1/ingestion/batches/{batch_id}/status`

## Endpoints principales

Base URL local: `http://localhost:8000/api/v1`

- `POST /ingestion/embed`: embeddings de texto.
- `POST /ingestion/ingest`: ingesta manual de archivo (`multipart/form-data`).
- `POST /ingestion/institutional`: ingesta institucional protegida por `X-Service-Secret`.
- `GET /ingestion/documents`: lista documentos fuente.
- `POST /ingestion/retry/{doc_id}`: reintenta documento con estado fallido.
- `POST /ingestion/batches`: crea batch 2-step para N archivos.
- `POST /ingestion/batches/{batch_id}/files`: agrega archivo al batch.
- `POST /ingestion/batches/{batch_id}/seal`: sella batch cuando se requiere cierre explicito.
- `GET /ingestion/batches/{batch_id}/status`: progreso del batch.
- `POST /knowledge/retrieve`: retrieval de contexto grounded.
- `POST /retrieval/chunks`: contrato v1 para retrieval de chunks.
- `POST /retrieval/summaries`: contrato v1 para retrieval de summaries.

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
- Baseline Visual Anchors: `runbooks/visual-anchor-baseline.md`
- Costos cloud ON/OFF: `runbooks/cloud-cost-on-off.md`

## Dependency management

- Direct dependencies are tracked in `requirements.in`.
- Frozen runtime dependencies are pinned in `requirements.txt`.
- Runtime install split:
  - `requirements-core.txt`: cloud/API baseline (no torch/transformers).
  - `requirements-local.txt`: local embedding runtime extras.
- Automated update pipeline is configured via `.github/dependabot.yml`.
