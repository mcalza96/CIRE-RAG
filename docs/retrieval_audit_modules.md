Módulo 1: Ingesta y Procesamiento de Documentos (ETL)
La calidad de la respuesta del LLM tiene un techo dictado por cómo procesas los PDFs y textos originales ("Basura entra, basura sale").
Flujo: Subida del documento -> Análisis estructural y visual -> Fragmentación (Chunking) -> Extracción de Grafo / Jerarquías (RAPTOR) -> Generación de Embeddings -> Guardado en Supabase.
Archivos clave a revisar:
app/infrastructure/document_parsers/pdf_parser.py y app/infrastructure/document_parsers/visual_parser.py (Manejo de tablas e imágenes).
app/domain/ingestion/chunking/splitter_strategies.py (El tamaño y solapamiento de tus chunks).
app/domain/ingestion/knowledge/raptor_processor.py y app/domain/ingestion/graph/graph_extractor.py (Construcción del conocimiento jerárquico y relacional).
¿Qué analizar para mejorar?
Pérdida de contexto en tablas/imágenes: ¿El parser está rompiendo el formato tabular? Revisa la calidad de visual_parser.
Estrategia de Chunking: Si los pedazos son muy pequeños, pierdes contexto global; si son muy grandes, diluyes la relevancia semántica. Prueba cambiar el tamaño de ventana y el solapamiento.

Módulo 2: Procesamiento y Enrutamiento de la Consulta (Query Understanding)
Antes de buscar, el sistema interpreta qué quiere el usuario, resuelve referencias de turnos anteriores de chat y decide a qué estrategia de recuperación enviar la consulta.
Flujo: Historial de Chat + Nueva Consulta -> Re-escritura/Fusión de la consulta -> Clasificación de Autoridad/Intención -> Validación del Scope.
Archivos clave a revisar:
app/api/v1/routers/chat.py (La función _build_retrieval_query reescribe la pregunta basándose en los últimos n turnos).
app/domain/ingestion/metadata/authority_classifier.py y app/domain/retrieval/strategies/agnostic_scope_strategy.py.
app/domain/retrieval/routing.py.
¿Qué analizar para mejorar?
Desambiguación: Asegúrate de que _build_retrieval_query genere una búsqueda autocontenida útil. Si el usuario dice "resume el artículo 8", la base de datos no sabe qué es "eso" si no se reescribe como "resume el artículo 8 de la norma ISO 9001".
Falsos positivos en Scopes: Revisa si el enrutador está descartando búsquedas legítimas al intentar forzar un filtro institucional restrictivo.

Módulo 3: Recuperación de Información (Retrieval Execution)
Aquí es donde los vectores y las bases de datos hacen el trabajo pesado para traer los candidatos crudos.
Flujo: RetrievalBroker -> Búsqueda Vectorial Pura o Híbrida (BM25 + Vector) -> Exploración de Grafo (GraphRAG).
Archivos clave a revisar:
app/workflows/retrieval/retrieval_broker.py y app/workflows/retrieval/plan_executor.py.
app/infrastructure/supabase/repositories/atomic_engine.py (Llamadas a la DB).
Funciones SQL en supabase/migrations/ (ej. 20260206_hybrid_retrieval_rpc.sql, 20260301_hybrid_search.sql).
¿Qué analizar para mejorar?
Búsqueda Híbrida: Si notas que términos exactos (ej. códigos de artículos, nombres de normativas) no traen resultados, probablemente necesitas ajustar el peso (alpha) del algoritmo de keyword search (BM25) frente a la búsqueda semántica densa en tus RPCs de Postgres.
K Inicial (Top-K): ¿Estás extrayendo suficientes documentos candidatos en esta primera fase para no perder el dato correcto antes de llegar al Re-ranker?

Módulo 4: Curación de Contexto y Re-Ranking (Post-Retrieval)
El paso crítico donde ordenamos los N documentos recuperados usando un modelo de machine learning más potente (cross-encoder) para elegir los mejores.
Flujo: Lista de candidatos inicial -> Re-Ranking (Jina o Gravity) -> Poda (Pruning) -> Mapeo final del contexto.
Archivos clave a revisar:
app/ai/rerankers/gravity_reranker.py y app/ai/rerankers/jina_reranker.py.
app/workflows/retrieval/grounded_retrieval.py (Curación final para responder con evidencia verificable).
¿Qué analizar para mejorar?
Calidad del modelo de Re-ranking: Revisa los umbrales de puntuación (score threshold). Si un chunk tiene baja puntuación, descártalo para no confundir al LLM ni gastar tokens.
Deduplicación: Si usas búsqueda híbrida + vectorial + RAPTOR, podrías estar pasando el mismo texto repetido varias veces.

Módulo 5: Generación Condicionada (Synthesis)
El LLM toma tu prompt, las directrices del sistema y el contexto curado para fabricar la respuesta.
Flujo: Prompt Base -> Inyección de chunks de contexto -> LLM Call -> Respuesta final con citas.
Archivos clave a revisar:
Directorio app/domain/prompts/ (ej. factual.py, narrative.py, citation_prompts.py).
app/ai/generation.py y app/ai/factory.py.
¿Qué analizar para mejorar?
Alucinaciones: ¿El LLM se sale del contexto? Refuerza tus system prompts con instrucciones más estrictas (ej. "Si la información no está en el contexto, responde 'No lo sé'").
Formato de Citas: Verifica que el LLM esté referenciando consistentemente las IDs generadas en el paso anterior.

Módulo 6: Observabilidad y Evaluación Dinámica
Lo que no se mide, no se puede mejorar.
Flujo: El usuario vota positivo/negativo -> Telemetría captura latencias y métricas -> Benchmarking automatizado con Golden Datasets.
Archivos clave a revisar:
tests/evaluation/custom_metrics.py y tests/evaluation/run_benchmark.py (Pipeline de evaluación offline).
app/infrastructure/observability/retrieval_metrics.py.
app/api/v1/routers/chat.py (Endpoint /chat/feedback).
¿Qué analizar para mejorar?
Feedback loop: Usa los comentarios de la ruta /chat/feedback para armar un dataset de prueba realista (golden_dataset). Corre tus benchmarks cada vez que cambies parámetros del Módulo 1 o 3.

Módulo 7: Seguridad, Control de Acceso y Multitenencia (Data Governance)
Asegurar que los usuarios solo accedan a los documentos permitidos por su tenant, para mantener la política estricta de aislamiento.
Flujo: Request autenticado -> Resolución de tenant -> Inyección de filtros RLS e institucionales -> Búsqueda Vectorial Segura.
Archivos clave a revisar:
app/api/middleware/security.py y app/api/v1/tenant_guard.py.
Conexiones seguras a BD (ej. políticas en supabase/migrations/).
app/api/v1/auth.py (resolución de credenciales y contexto S2S).
¿Qué analizar para mejorar?
Filtrado previo vs. posterior (Pre-filtering): Siempre filtra por tenant_id y permisos antes o durante la búsqueda vectorial.
Vulnerabilidades de Scope: Si el RAG tiene acceso a múltiples particiones, asegúrate de que el router no permita omitir filtros por prompt injection.

Módulo 8: Orquestación Avanzada y Flujos Iterativos
Si el caso de uso se vuelve complejo, la cadena lineal de RAG debe convertirse en un flujo con capacidades de evaluación y auto-corrección.
Flujo: Workflow recibe request -> Plan -> Retrieve -> Reflect/Repair -> Respuesta final.
Archivos clave a revisar:
app/workflows/retrieval/retrieval_broker.py y app/workflows/retrieval/plan_executor.py.
app/domain/retrieval/planning.py y app/domain/retrieval/validation.py.
Manejo de estado y trazas en app/domain/retrieval/tracing.py.
¿Qué analizar para mejorar?
Loops infinitos y timeouts: Asegura un tope máximo de repeticiones de reflexión o intentos de recuperación y un control de tiempo global para la transacción.
Retroceso y Degradación Elegante (Graceful Degradation): Si una estrategia falla, estructura fallbacks para recuperar información parcial o un aviso amigable y rápido para el usuario.
