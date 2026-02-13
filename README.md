# CISRE (Cognitive Ingestion & Structured Retrieval Engine)

Open-source and API-first back-end engine for high-precision RAG (Retrieval-Augmented Generation) and audit workflows.

---

## English Version

### Vision

CISRE does not flatten content indiscriminately: it treats document structure as a first-class data type, enabling robust reasoning over tables, figures, and relationships between documents.

### Architecture Philosophy

CISRE prioritizes operational simplicity and deterministic results over agentic complexity.

- **Cognitive Ingestion (Visual Anchors)**: Uses VLMs (e.g., Gemini 2.5 Flash Lite) to parse tables/figures into structured JSON. Applies **Dual-Model Extraction**: defaults to high-speed LITE model and automatically escalates to full FLASH on technical parse errors.
- **Atomic Retrieval (Atomic Engine)**: Dynamic orchestration via `QueryDecomposer` that combines Vector Search, Full-Text Search (FTS), and multi-hop graph navigation in a single atomic retrieval phase.
- **Unified Stack**: Python 3.13 + Supabase (Postgres + pgvector), without separating vector DB and graph DB into different products.

### AI-First Design Philosophy

This repository may appear "over-engineered" from a traditional human perspective due to its high fragmentation and extensive use of interfaces. However, this structure is **intentional** and serves as a **high-speed rail for AI-assisted programming**.

- **Interfaces as Guardrails**: Abstract classes and interfaces limit AI hallucination, defining explicit contracts that agents must respect.
- **Structural Prompt Engineering**: Atomization allows the AI to work on small, specialized contexts, increasing the quality of generated code.
- **Security by Design**: We separate dynamic orchestration (Python) from immutable data processing (SQL in Supabase), where the system's "muscle" remains efficient and secure.

We don't just write code for humans; we write **code to be safely extended by machines**. We sacrifice "visual simplicity" to gain **traceability, extensibility, and synthetic iteration speed**.

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
- Async worker with pull model over Supabase `job_queue` (`fetch_next_job`).
- Human-in-the-loop response gating: asks for scope clarification before answering when query intent is ambiguous or conflicting.

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

- **Ingesta Cognitiva (Visual Anchors)**: Usa VLMs (ej. Gemini 2.5 Flash Lite) para parsear tablas/figuras en JSON estructurado. Aplica **Dual-Model Extraction**: usa el modelo LITE por defecto y escala automáticamente al modelo FLASH completo ante errores técnicos de parseo.
- **Retrieval Atómico (Atomic Engine)**: Orquestación dinámica mediante `QueryDecomposer` que combina Vector Search, Full-Text Search (FTS) y navegación de grafos multi-hop en una única fase de búsqueda atómica.
- **Stack Unificado**: Python 3.13 + Supabase (Postgres + pgvector), sin separar la base de datos vectorial y de grafos en productos distintos.

### Filosofía de Diseño AI-First

Este repositorio puede parecer "sobre-ingenierizado" bajo una mirada humana tradicional debido a su alta fragmentación y uso extensivo de interfaces. Sin embargo, esta estructura es **intencional** y constituye una **carretera de alta velocidad para la programación asistida por IA**.

- **Interfaces como Guardrails**: Las clases abstractas e interfaces limitan la alucinación de la IA, definiendo contratos explícitos que los agentes deben respetar.
- **Ingeniería de Prompts Estructural**: La atomización permite que la IA trabaje en contextos pequeños y especializados, aumentando la calidad del código generado.
- **Seguridad por Diseño**: Separamos la orquestación dinámica (Python) del procesamiento de datos inmutable (SQL en Supabase), donde el "músculo" del sistema permanece eficiente y seguro.

No escribimos código solo para humanos; escribimos **código para ser extendido por máquinas de forma segura**. Sacrificamos la "simplicidad visual" para ganar en **trazabilidad, extensibilidad y velocidad de iteración sintética**.

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
- Worker asíncrono en modelo pull sobre `job_queue` de Supabase (`fetch_next_job`).
- Human-in-the-loop en respuestas: solicita aclaración de alcance antes de responder cuando la intención de consulta es ambigua o conflictiva.

### Principios

- Evidencia antes que fluidez.
- Estructura antes que embeddings ciegos.
- SQL nativo antes que infraestructura fragmentada.
- Determinismo antes que agentes autónomos.

---

## Technical Details / Detalles Técnicos

### Tech Stack / Stack Tecnológico

- Python 3.13+
- FastAPI + Uvicorn
- Supabase (PostgreSQL + pgvector + RPC/job_queue)
- LangChain + LangGraph
- Jina Embeddings v3
- Gemini (Native Lite + Fallback Flash) / Groq / OpenAI adapters
- DSPy (prompt/model optimization workflows)
- Pytest + DeepEval

### Repository Layout / Estructura del Repositorio

- `app`: API, domain, services, workflows, infrastructure.
- `tests`: Unit, integration, stress, evaluation suites.
- `docs/rag-engine`: Engine-specific architecture, runbooks, and contracts.
- `supabase/migrations`: SQL migrations.
- `.agent/rules/reglas.md`: Operational guardrails for AI-agent behavior.

Documentation hub: `docs/README.md`.

### Quickstart / Inicio Rápido

```bash
cd .
cp .env.example .env.local
./bootstrap.sh
./start_api.sh
```

Run worker in a second terminal:

```bash
cd .
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

Full details: `docs/rag-engine/configuration.md`.

### Testing

```bash
cd .
venv/bin/pytest tests/unit -q
venv/bin/pytest tests/integration -q
```

Evaluation workflows: `tests/evaluation`.

### Contributing / Contribuciones

- Open an issue for bugs, proposals, or feature requests.
- Submit focused PRs with tests for changed behavior.
- Keep docs in sync when ingestion/retrieval workflows change.

See `CONTRIBUTING.md` for workflow and quality gates.

### License / Licencia

This project is licensed under Apache License 2.0.
Este proyecto se distribuye bajo licencia Apache 2.0.

See `LICENSE` for details.
