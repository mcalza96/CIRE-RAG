# CISRE (Cognitive Ingestion & Structured Retrieval Engine)

Motor back-end open-source y API-first para flujos RAG (Retrieval-Augmented Generation) de alta precision y auditoria.

## Vision

CISRE no aplana contenido indiscriminadamente: trata la estructura del documento como dato de primera clase, habilitando razonamiento robusto sobre tablas, figuras y relaciones entre documentos.

## Filosofia de Arquitectura

CISRE prioriza simplicidad operativa y resultados deterministas sobre complejidad agentica.

- **Ingesta Cognitiva (Visual Anchors)**: usa VLMs (ej. Gemini Flash) para parsear tablas/figuras en JSON estructurado. Aplica **Late Binding**: indexa estructura semantica e hidrata contexto visual solo cuando se necesita.
- **Orquestacion Tricameral**: clasifica intencion de consulta y enruta dinamicamente entre:
  - `Vector Search` para similitud semantica directa.
  - `GraphRAG (SQL-Native)` para relaciones y multi-hop ejecutado en Postgres.
  - `RAPTOR` para resumenes jerarquicos y preguntas de alto nivel.
- **Stack Unificado**: Python + Supabase (Postgres + pgvector), sin separar vector DB y graph DB en productos distintos.

## Caso de Uso

Agnostico al dominio. Disenado para contextos donde la precision es critica (legal, financiero, tecnico, normativo/compliance) y donde el naive RAG falla por estructura densa o dependencias logicas.

## Capacidades Principales

- PDF structural extraction with `pymupdf4llm` and PyMuPDF fallback.
- Visual parsing pipeline for tables/figures with deterministic structured output.
- Jina Embeddings v3 in local or cloud mode.
- Late chunking with contextual fallback by heading hierarchy.
- Tricameral routing (`SPECIFIC | GENERAL | HYBRID`) for retrieval strategy.
- RAPTOR-style hierarchical summaries for broad and exploratory queries.
- GraphRAG local/global retrieval plus authority-based reranking.
- Async worker integrated with Supabase Realtime events.

## Principios

- Evidencia antes que fluidez.
- Estructura antes que embeddings ciegos.
- SQL-native antes que infraestructura fragmentada.
- Determinismo antes que agentes autonomos.

## Tech Stack / Stack Tecnologico

- Python 3.11+
- FastAPI + Uvicorn
- Supabase (PostgreSQL + pgvector + Realtime)
- LangChain + LangGraph
- Jina Embeddings v3
- Gemini / Groq / OpenAI adapters (provider-agnostic model layer)
- DSPy (prompt/model optimization workflows)
- Pytest + DeepEval

## Repository Layout / Estructura del Repositorio

- `rag-ingestion`: main service codebase / codigo principal del servicio.
- `rag-ingestion/app`: API, domain, services, workflows, infrastructure.
- `rag-ingestion/tests`: unit, integration, stress, evaluation suites.
- `supabase/migrations`: SQL migrations.
- `.agent/rules/reglas.md`: operational guardrails for AI-agent behavior.

Documentation hub / Centro de documentacion: `docs/README.md`.

## Quickstart / Inicio Rapido

```bash
cd rag-ingestion
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

Run worker in a second terminal / Corre el worker en otra terminal:

```bash
cd rag-ingestion
venv/bin/python run_worker.py
```

Health check / Verificacion de salud:

```bash
curl http://localhost:8000/health
```

CLI workflow (tenant + folder scoped):

```bash
./ing.sh
./chat.sh
```

## Configuration / Configuracion

Minimum required variables / Variables minimas requeridas:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Common optional providers / Proveedores opcionales comunes:

- `JINA_API_KEY` (Jina Cloud mode)
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`

Full details / Detalle completo: `rag-ingestion/docs/configuration.md`.

## API Overview / Resumen de API

Base path / Ruta base: `/api/v1`

- `POST /ingestion/ingest`
- `POST /ingestion/institutional`
- `POST /knowledge/retrieve`
- `POST /synthesis/generate`
- `GET /synthesis/jobs/{job_id}`
- `POST /curriculum/generate` (legacy path for structured synthesis jobs)
- `GET /curriculum/jobs/{job_id}` (legacy path for structured synthesis jobs)

More details / Mas detalle: `rag-ingestion/README.md`.

## Testing

```bash
cd rag-ingestion
venv/bin/pytest tests/unit -q
venv/bin/pytest tests/integration -q
```

Evaluation workflows / Flujos de evaluacion: `rag-ingestion/tests/evaluation`.

## Documentation / Documentacion

- Start here / Empieza aqui: `docs/README.md`
- Getting started: `docs/getting-started.md`
- Developer workflow: `docs/developer-guide.md`
- Architecture map: `docs/architecture.md`
- Operations and runbooks: `docs/operations.md`

## Contributing / Contribuciones

EN:
- Open an issue for bugs, proposals, or feature requests.
- Submit focused PRs with tests for changed behavior.
- Keep docs in sync when ingestion/retrieval workflows change.

ES:
- Abre issues para bugs, propuestas o nuevas funcionalidades.
- Envia PRs enfocados con pruebas para el comportamiento modificado.
- Mantiene la documentacion sincronizada cuando cambien los flujos de ingesta/retrieval.

See `CONTRIBUTING.md` for workflow, quality gates, and support matrix.

## License / Licencia

This project is licensed under Apache License 2.0.
Este proyecto se distribuye bajo licencia Apache 2.0.

See `LICENSE` for details.
