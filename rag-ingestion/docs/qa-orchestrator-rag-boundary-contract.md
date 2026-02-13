# Q/A Orchestrator <-> RAG Boundary Contract

## Objetivo

Definir una frontera explicita para que `app/qa_orchestrator` (bibliotecario)
coexista con el backend RAG (biblioteca) sin duplicar responsabilidades.

Nombre historico del modulo: `app/mas_simple`.

## Modelo mental

- RAG (`app/application`, `app/services`, `app/infrastructure`): sistema de conocimiento.
- Q/A Orchestrator (`app/qa_orchestrator`): sistema de orquestacion de pregunta/respuesta.

Regla principal: **Q/A Orchestrator decide; RAG recupera y persiste conocimiento**.

## Responsabilidades por lado

### RAG (Biblioteca)

- Ingestion, chunking, embeddings, grafo, RAPTOR.
- Retrieval (chunks + summaries) con filtros de tenant y coleccion.
- Persistencia y trazabilidad en Supabase.
- Politicas de consistencia de datos y performance.

### Q/A Orchestrator (Bibliotecario)

- Clasificar intencion de consulta.
- Construir plan de retrieval (`RetrievalPlan`).
- Pedir evidencia a traves de puertos (`RetrieverPort`).
- Generar respuesta y validar cobertura/scope.
- Devolver respuesta final al cliente.

## Dependencias permitidas (import boundary)

### Permitido en Q/A Orchestrator

- Modelos y politicas de `app/qa_orchestrator/*`.
- Puertos definidos en `app/qa_orchestrator/ports.py`.
- Adaptadores de infraestructura que consumen herramientas de retrieval ya existentes.

### No permitido en Q/A Orchestrator

- SQL inline o acceso directo a Supabase client.
- Logica de ingestion/chunking/embedding.
- Escritura directa de entidades RAG internas.

## Contrato de datos minimo

### Request hacia retrieval

- `query: str`
- `tenant_id: str`
- `collection_id: str | None`
- `plan: RetrievalPlan`

### Response desde retrieval

Lista de `EvidenceItem` con:

- `source` (ej: `C1`, `R2`)
- `content`
- `score`
- `metadata.row` (registro crudo para trazabilidad)

## Contrato de errores

- RAG unavailable/timeouts: Q/A Orchestrator responde fallback controlado, no stacktrace.
- Sin evidencia: respuesta explicita de "no encontrado en contexto".
- Scope mismatch: respuesta bloqueada o advertida por validador.

## SLO operativo sugerido

- p95 retrieval chunks: <= 1.5s
- p95 retrieval summaries: <= 1.5s
- p95 end-to-end Q/A Orchestrator: <= 4s (sin streaming)
- Tasa de respuestas sin evidencia (modo literal): < 5% semanal

## Versionado del contrato

- Version inicial: `v1`.
- Cambios breaking en estructura de `EvidenceItem` o semantica de filtros requieren `v2`.
- Mantener compatibilidad backward por al menos 1 sprint entre versiones.

## Plan de gobernanza

- Un owner tecnico por lado (Q/A Orchestrator y RAG).
- PR checklist obligatorio:
  1. Esta logica pertenece al bibliotecario o a la biblioteca?
  2. Se esta rompiendo el contrato de datos?
  3. Hay test de integracion para el cambio?
- Review quincenal de metricas: latencia, recall, scope mismatch, errores.

## Criterios de exito

- Q/A Orchestrator no importa Supabase client.
- RAG no conoce detalle de prompts de Q/A Orchestrator.
- Cualquier cambio de retrieval se hace en adaptador, no en el core de Q/A Orchestrator.
- Se puede evolucionar cada lado con deploy independiente.
