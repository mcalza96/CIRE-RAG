# CISRE (Cognitive Ingestion & Structured Retrieval Engine)

Open-source and API-first back-end engine for high-precision RAG (Retrieval-Augmented Generation) and audit workflows.

---

## English Version

### Vision

CISRE does not flatten content indiscriminately: it treats document structure as a first-class data type, enabling robust reasoning over tables, figures, and relationships between documents.

### Architecture Philosophy

CISRE prioritizes operational simplicity and deterministic results over agentic complexity.

- **Cognitive Ingestion (Visual Anchors)**: Uses VLMs (e.g., Gemini Flash) to parse tables/figures into structured JSON. Applies **Late Binding**: indexes semantic structure and hydrates visual context only when needed.
- **Tricameral Orchestration**: Classifies query intent and dynamically routes between:
  - `Vector Search` for direct semantic similarity.
  - `GraphRAG (SQL-Native)` for relationships and multi-hop queries executed in Postgres.
  - `RAPTOR` for hierarchical summaries and high-level questions.
- **Unified Stack**: Python + Supabase (Postgres + pgvector), without separating vector DB and graph DB into different products.

### Use Case

Domain agnostic. Designed for contexts where precision is critical (legal, financial, technical, regulatory/compliance) and where naive RAG fails due to dense structure or logical dependencies.

### Core Capabilities

- PDF structural extraction with `pymupdf4llm` and PyMuPDF fallback.
- Visual parsing pipeline for tables/figures with deterministic structured output.
- Jina Embeddings v3 in local or cloud mode.
- Late chunking with contextual fallback by heading hierarchy.
- Tricameral routing (`SPECIFIC | GENERAL | HYBRID`) for retrieval strategy.
- RAPTOR-style hierarchical summaries for broad and exploratory queries.
- GraphRAG local/global retrieval plus authority-based reranking.
- Async worker integrated with Supabase Realtime events.

### Principles

- Evidence before fluency.
- Structure before blind embeddings.
- SQL-native before fragmented infrastructure.
- Determinism before autonomous agents.

---

## Versión en Español

### Visión

CISRE no aplana el contenido indiscriminadamente: trata la estructura del documento como un tipo de dato de primera clase, permitiendo un razonamiento robusto sobre tablas, figuras y relaciones entre documentos.

### Filosofía de Arquitectura

CISRE prioriza la simplicidad operativa y los resultados deterministas sobre la complejidad agéntica.

- **Ingesta Cognitiva (Visual Anchors)**: Usa VLMs (ej. Gemini Flash) para parsear tablas/figuras en JSON estructurado. Aplica **Late Binding**: indexa la estructura semántica e hidrata el contexto visual solo cuando es necesario.
- **Orquestación Tricameral**: Clasifica la intención de la consulta y enruta dinámicamente entre:
  - `Búsqueda Vectorial` para similitud semántica directa.
  - `GraphRAG (SQL Nativo)` para relaciones y consultas de múltiples saltos ejecutadas en Postgres.
  - `RAPTOR` para resúmenes jerárquicos y preguntas de alto nivel.
- **Stack Unificado**: Python + Supabase (Postgres + pgvector), sin separar la base de datos vectorial y de grafos en productos distintos.

### Casos de Uso

Agnóstico al dominio. Diseñado para contextos donde la precisión es crítica (legal, financiero, técnico, normativo/compliance) y donde el RAG convencional falla debido a estructuras densas o dependencias lógicas.

### Capacidades Principales

- Extracción estructural de PDFs con `pymupdf4llm` y fallback a PyMuPDF.
- Pipeline de parseo visual para tablas/figuras con salida estructurada determinista.
- Jina Embeddings v3 en modo local o nube.
- Fragmentación tardía (late chunking) con fallback contextual por jerarquía de encabezados.
- Enrutamiento tricameral (`ESPECÍFICO | GENERAL | HÍBRIDO`) para la estrategia de recuperación.
- Resúmenes jerárquicos estilo RAPTOR para consultas amplias y exploratorias.
- Recuperación local/global mediante GraphRAG más reranking basado en autoridad.
- Worker asíncrono integrado con eventos en tiempo real de Supabase.

### Principios

- Evidencia antes que fluidez.
- Estructura antes que embeddings ciegos.
- SQL nativo antes que infraestructura fragmentada.
- Determinismo antes que agentes autónomos.

---

## Technical Details / Detalles Técnicos

### Tech Stack / Stack Tecnológico

- Python 3.11+
- FastAPI + Uvicorn
- Supabase (PostgreSQL + pgvector + Realtime)
- LangChain + LangGraph
- Jina Embeddings v3
- Gemini / Groq / OpenAI adapters (provider-agnostic model layer)
- DSPy (prompt/model optimization workflows)
- Pytest + DeepEval

### Repository Layout / Estructura del Repositorio

- `rag-ingestion`: Main service codebase.
- `rag-ingestion/app`: API, domain, services, workflows, infrastructure.
- `rag-ingestion/tests`: Unit, integration, stress, evaluation suites.
- `supabase/migrations`: SQL migrations.
- `.agent/rules/reglas.md`: Operational guardrails for AI-agent behavior.

Documentation hub: `docs/README.md`.

### Quickstart / Inicio Rápido

```bash
cd rag-ingestion
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

Run worker in a second terminal:

```bash
cd rag-ingestion
venv/bin/python run_worker.py
```

Health check:
`curl http://localhost:8000/health`

CLI workflow:
`./ing.sh`
`./chat.sh`

### Configuration / Configuración

Minimum required variables:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional providers:
- `JINA_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`

Full details: `rag-ingestion/docs/configuration.md`.

### Testing

```bash
cd rag-ingestion
venv/bin/pytest tests/unit -q
venv/bin/pytest tests/integration -q
```

Evaluation workflows: `rag-ingestion/tests/evaluation`.

### Contributing / Contribuciones

- Open an issue for bugs, proposals, or feature requests.
- Submit focused PRs with tests for changed behavior.
- Keep docs in sync when ingestion/retrieval workflows change.

See `CONTRIBUTING.md` for workflow and quality gates.

### License / Licencia

This project is licensed under Apache License 2.0.
Este proyecto se distribuye bajo licencia Apache 2.0.

See `LICENSE` for details.

