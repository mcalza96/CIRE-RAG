Módulo 1: Ingesta y Procesamiento de Documentos (ETL)
La calidad de la respuesta del LLM tiene un techo dictado por cómo procesas los PDFs y textos originales ("Basura entra, basura sale").
Flujo: Subida del documento → Análisis estructural y visual → Fragmentación (Chunking) → Extracción de Grafo / Jerarquías (RAPTOR) → Generación de Embeddings → Guardado en Supabase.
Archivos clave a revisar:
app/services/ingestion/pdf_parser.py y visual_parser.py (Manejo de tablas e imágenes).
app/services/ingestion/chunking/splitter_strategies.py (El tamaño y solapamiento de tus chunks).
app/services/knowledge/raptor_processor.py y graph_extractor.py (Construcción del conocimiento jerárquico y relacional).
¿Qué analizar para mejorar?
Pérdida de contexto en tablas/imágenes: ¿El parser está rompiendo el formato tabular? Revisa la calidad de visual_parser.
Estrategia de Chunking: Si los pedazos son muy pequeños, pierdes contexto global; si son muy grandes, diluyes la relevancia semántica. Prueba cambiar el tamaño de ventana y el solapamiento.
Módulo 2: Procesamiento y Enrutamiento de la Consulta (Query Understanding)
Antes de buscar, el sistema interpreta qué quiere el usuario, resuelve referencias de turnos anteriores de chat y decide a qué "motor" preguntarle.
Flujo: Historial de Chat + Nueva Consulta → Re-escritura/Fusión de la consulta → Clasificación de Autoridad/Intención → Validación del Scope (ej. filtrar por ISO).
Archivos clave a revisar:
app/api/v1/routers/chat.py (La función _build_retrieval_query reescribe la pregunta basándose en los últimos n turnos).
app/domain/services/authority_classifier.py y app/services/knowledge/iso_scope_strategy.py.
app.services.retrieval.routing.router.py.
¿Qué analizar para mejorar?
Desambiguación: Asegúrate de que _build_retrieval_query genere una búsqueda autocontenida útil. Si el usuario dice "resume el artículo 8", la base de datos no sabe qué es "eso" si no se reescribe como "resume el artículo 8 de la norma ISO 9001".
Falsos positivos en Scopes: Revisa si el enrutador está descartando búsquedas legítimas al intentar forzar un filtro institucional restrictivo.
Módulo 3: Recuperación de Información (Retrieval Execution)
Aquí es donde los vectores y las bases de datos hacen el trabajo pesado para traer los candidatos crudos.
Flujo: Motor RAG (Retrieval Broker) → Búsqueda Vectorial Pura o Híbrida (BM25 + Vector) → Exploración de Grafo (GraphRAG).
Archivos clave a revisar:
app.services.retrieval.orchestration.retrieval_broker.py y app/services/retrieval/retrieval_plan_executor.py.
app/services/retrieval/atomic_engine.py (Llamadas a la DB).
Funciones SQL en supabase/migrations/ (ej. 20260206_hybrid_retrieval_rpc.sql, 20260301_hybrid_search.sql).
¿Qué analizar para mejorar?
Búsqueda Híbrida: Si notas que términos exactos (ej. códigos de artículos, nombres de normativas) no traen resultados, probablemente necesitas ajustar el peso (alpha) del algoritmo de keyword search (BM25) frente a la búsqueda semántica densa en tus RPCs de Postgres.
K Inicial (Top-K): ¿Estás extrayendo suficientes documentos candidatos en esta primera fase para no perder el dato correcto antes de llegar al Re-ranker?
Módulo 4: Curación de Contexto y Re-Ranking (Post-Retrieval)
El paso crítico donde ordenamos los N documentos recuperados usando un modelo de machine learning más potente (cross-encoder) para elegir los mejores.
Flujo: Lista de candidatos inicial → Re-Ranking (Jina o Gravity) → Poda (Pruning) → Mapeo final del contexto.
Archivos clave a revisar:
app/services/knowledge/gravity_reranker.py y jina_reranker.py.
app/services/knowledge/knowledge_service.py (La curación final que mapea las citas context_chunks).
¿Qué analizar para mejorar?
Calidad del modelo de Re-ranking: Revisa los umbrales de puntuación (score threshold). Si un chunk tiene baja puntuación, descártalo para no confundir al LLM ni gastar tokens.
Deduplicación: Si usas búsqueda híbrida + vectorial + RAPTOR, podrías estar pasando el mismo texto repetido 3 veces.
Módulo 5: Generación Condicionada (Synthesis)
El LLM toma tu prompt, las directrices del sistema y el contexto curado para fabricar la respuesta.
(Nota: Tu endpoint de chat expone los chunks directamente, actuando como un orquestador backend, pero también cuentas con lógica de prompts internos).
Flujo: Prompt Base → Inyección de chunks de contexto → LLM Call → Respuesta final con citas.
Archivos clave a revisar:
Directorio app/core/prompts/ (e.g. factual.py, narrative.py, citation_prompts.py).
app/core/llm.py y app/core/structured_generation.py.
¿Qué analizar para mejorar?
Alucinaciones: ¿El LLM se sale del contexto? Refuerza tus system prompts con instrucciones más estrictas (e.g., "Si la información no está en el contexto, responde 'No lo sé'").
Formato de Citas: Verifica que el LLM esté referenciando consistentemente las IDs generadas en el paso anterior.
Módulo 6: Observabilidad y Evaluación Dinámica
Lo que no se mide, no se puede mejorar.
Flujo: El usuario vota positivo/negativo → Telemetría captura latencias y métricas → Benchmarking automatizado con Golden Datasets.
Archivos clave a revisar:
tests/evaluation/custom_metrics.py y run_benchmark.py (Tu pipeline de evaluación offline).
app/core/observability/retrieval_metrics.py.
app/api/v1/routers/chat.py (Endpoint /chat/feedback).
¿Qué analizar para mejorar?
Feedback loop: Usa los comentarios de la ruta /chat/feedback para armar un Dataset de prueba realista (golden_dataset.json). Corre tus scripts de benchmarks/ cada vez que cambies parámetros del Módulo 1 o 3.

Módulo 7: Seguridad, Control de Acceso y Multitenencia (Data Governance)
Asegurar que los usuarios solo accedan a los documentos permitidos por su rol, institución o tenant, para mantener la política estricta de aislamiento de TeacherOS.
Flujo: Request de Autenticación → Resolución del Perfil/Rol → Inyección de Filtros RLS (Row Level Security) e Institucionales → Búsqueda Vectorial Segura.
Archivos clave a revisar:
app/core/auth_client.py y dependencias de JWT/Autenticación.
Conexiones seguras a BD (e.g., config de aislamiento en supabase/migrations/).
app/application/services/tenant_isolation.py (Mapeos y políticas del usuario).
¿Qué analizar para mejorar?
Filtrado previo vs. posterior (Pre-filtering): Siempre filtra por `tenant_id` y permisos *antes* o *durante* la búsqueda vectorial (Pre-filtering), es más eficiente y seguro que descartar resultados al final.
Vulnerabilidades de Scope: Si el RAG tiene acceso a la partición general y a la institucional, asegúrate de que el router no permita, por medio de prompt injection, omitir los filtros institucionales.

Módulo 8: Orquestación Avanzada y Flujos Multi-Agente (Orchestrator Layer)
Si el caso de uso se vuelve complejo, la cadena lineal de RAG debe convertirse en un Grafo con capacidades de evaluación y auto-corrección (LangGraph/Agentes).
Flujo: Orchestrator recibe request → Grafo de decisiones (Plan -> Retrieve -> Reflect) → Bucle si se requiere refinar la búsqueda → Respuesta final.
Archivos clave a revisar:
Orquestador Principal, como app/agent/universal_flow.py.
Nodos específicos del grafo (e.g., plan_node.py, retrieve_node.py, reflect_node.py).
Manejo de estados (State) del Grafo.
¿Qué analizar para mejorar?
Loops infinitos y timeouts: Asegura un tope máximo de repeticiones de reflexión o intentos de recuperación y un control de tiempo global para la transacción, ya que las cadenas de RAG iterativas pueden agotar el presupuesto de concurrencia y los límites del servidor.
Retroceso y Degradación Elegante (Graceful Degradation): Si un sub-agente falla en la extracción, ¿falla toda la respuesta? Estructura fallbacks para recuperar al menos información parcial o un aviso amigable y rápido para el usuario.