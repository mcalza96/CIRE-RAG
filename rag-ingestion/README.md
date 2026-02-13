# RAG Ingestion + Q/A Orchestrator

Motor de servicio de CISRE para ingesta cognitiva y retrieval estructurado con trazabilidad.

Disenado para operar como backend API-first en escenarios donde el naive RAG falla con tablas, figuras y dependencias entre documentos.

## Componentes principales

- `RAG backend`: ingestion, persistencia, retrieval hibrido y worker.
- `Q/A Orchestrator` (`app/qa_orchestrator`): capa de bibliotecario para planificar consulta, generar respuesta y validar evidencia.
- Contrato entre capas: `docs/qa-orchestrator-rag-boundary-contract.md`.

## Filosofia operativa

- **Ingesta Cognitiva (Visual Anchors)**: el pipeline visual parsea tablas/figuras a JSON estructurado. Aplica **Dual-Model Extraction** (Lite + Flash fallback).
- **Retrieval Atómico (Atomic Engine)**: orquestación dinámica mediante `QueryDecomposer` que combina Vector Search, Full-Text Search (FTS) y navegación de grafos multi-hop en una única fase de búsqueda atómica.
- **RAPTOR (cuando aplica)**: construye un arbol jerarquico de resumenes mediante clustering semantico recursivo (no depende de estructura fija pagina/capitulo).
- **Stack unificado**: FastAPI + Supabase (Postgres 17 + pgvector), sin fragmentar en motores separados.

## Arquitectura para Agentes de IA (AI-First Engineering)

Este repositorio puede parecer "sobre-ingenierizado" bajo una mirada humana tradicional debido a su alta fragmentación y uso extensivo de interfaces. Sin embargo, esta estructura es **intencional** y constituye una **carretera de alta velocidad para la programación asistida por IA**.

- **Interfaces como Guardrails**: Las clases abstractas e interfaces limitan la alucinación de la IA, definiendo contratos explícitos que los agentes deben respetar.
- **Ingeniería de Prompts Estructural**: La atomización permite que la IA trabaje en contextos pequeños y especializados, aumentando la calidad del código generado.
- **Seguridad por Diseño**: Separamos la orquestación dinámica (Python) del procesamiento de datos inmutable (SQL en Supabase), donde el "músculo" del sistema permanece eficiente y seguro.

No escribimos código solo para humanos; escribimos **código para ser extendido por máquinas de forma segura**. Sacrificamos la "simplicidad visual" para ganar en **trazabilidad, extensibilidad y velocidad de iteración sintética**.

## Flujo operativo actual

1. **Ingestion**: endpoints de ingesta registran documento en `queued` y devuelven snapshot de cola.
2. **Proceso**: `run_worker.py` consume `job_queue` en modo pull (`fetch_next_job`) y ejecuta pipeline de parseo/chunking/embeddings/persistencia, con Visual Anchors + RAPTOR + Graph opcionales.
3. **Pregunta**: consultas por `/knowledge/*` o `doc_chat_cli.py`.
4. **Analisis**: `Q/A Orchestrator` clasifica intencion, define plan de retrieval, detecta ambiguedad/conflicto de scope y valida evidencia.
5. **Respuesta**: aplica Human-in-the-Loop (HITL): si falta claridad de alcance pide aclaracion al usuario; con aclaracion confirmada responde grounded con trazabilidad C#/R#; si persiste inconsistencia bloquea la respuesta.

## Human-in-the-Loop en respuestas

- El orquestador puede emitir `ClarificationRequest` antes de responder cuando detecta escenario multinorma o conflicto entre objetivos (ej. confidencialidad vs trazabilidad).
- En `doc_chat_cli.py`, la respuesta de aclaracion del usuario se interpreta y se reinyecta en la consulta (`__clarified_scope__=true`) para una segunda pasada controlada.
- Si la validacion detecta `Scope mismatch` no recuperable, se bloquea la respuesta final y se solicita reformulacion explicita de la norma objetivo.

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
- `POST /knowledge/answer`: respuesta grounded final con gating HITL por scope.
- `POST /synthesis/generate`: crea job asyncrono de sintesis estructurada.
- `GET /synthesis/jobs/{job_id}`: estado del job de sintesis estructurada.
- `POST /curriculum/generate`: crea job asyncrono de sintesis estructurada (ruta legacy).
- `GET /curriculum/jobs/{job_id}`: estado del job de sintesis estructurada (ruta legacy).

## Comportamiento HITL en `/knowledge/*`

- Si el sistema detecta alcance ambiguo, devuelve aclaracion en vez de respuesta final (`requires_scope_clarification` en `/knowledge/retrieve`, o `answer` con mensaje de aclaracion en `/knowledge/answer`).
- Si detecta inconsistencia de ambito entre pregunta y fuentes, bloquea respuesta final y pide reformulacion explicita de norma.
- Si el scope es valido, responde grounded con `citations` y `mode`.

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

Ejemplo (scope mismatch bloqueado en `/knowledge/answer`):

```json
{
  "answer": "⚠️ Se detectó inconsistencia de ámbito entre la pregunta y las fuentes recuperadas...",
  "context_chunks": [],
  "citations": [],
  "mode": "HYBRID"
}
```

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
- Getting started HITL: `docs/getting-started.md`
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
