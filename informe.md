Estrategia Técnica: Implementación de Recuperación Multi-Salto de Alta Velocidad en CISRE
Resumen Ejecutivo
Este informe técnico detalla la estrategia de reingeniería arquitectónica necesaria para dotar al sistema CISRE de capacidades de "Multi-hop Retrieval" (Recuperación de Múltiples Saltos) sin comprometer los Acuerdos de Nivel de Servicio (SLA) de latencia crítica (<3 segundos). Actualmente, la arquitectura de CISRE opera bajo un paradigma de paralelismo independiente a través de un "Orquestador Tricameral", el cual es altamente eficiente para consultas de agregación de facetas pero fundamentalmente defectuoso para consultas que implican dependencias lógicas secuenciales ().
La investigación y el análisis de las mejores prácticas de ingeniería, contrastadas con las capacidades específicas del stack tecnológico existente (Python/FastAPI + Supabase/Postgres), indican que la solución óptima no reside en la adopción de agentes autónomos complejos (tipo LangGraph), cuya latencia es prohibitiva para producción en tiempo real. En su lugar, se propone una arquitectura de "Inferencia Anticipada y Traversalidad Nativa".
Esta estrategia se fundamenta en tres pilares técnicos:
Descomposición Predictiva de Consultas: Evolución del orquestador actual hacia un modelo de planificación ligera ("Lightweight Planning") que utiliza Few-Shot Prompting para desglosar dependencias lógicas en un solo paso de inferencia.
Travesía de Grafo SQL-Nativa: Explotación de la capacidad de PostgreSQL y extensiones como pgvector para ejecutar saltos lógicos (2-hop) directamente en la capa de datos, eliminando la latencia de red (Network I/O) y serialización asociada a la lógica de aplicación en Python.3
Recuperación Híbrida Look-Ahead: Un modelo de ejecución que prefiere la recuperación de vecindarios semánticos ampliados sobre la iteración secuencial estricta, equilibrando la precisión del razonamiento con la velocidad de ejecución.5
La implementación de estas estrategias permitirá a CISRE resolver consultas complejas como "¿Cuál es el capital social de la empresa subsidiaria mencionada en la tabla de resultados del Q3?" en una sola operación orquestada, manteniendo la infraestructura actual y maximizando el retorno de inversión en el stack de Supabase.
1. Diagnóstico Arquitectónico: La Brecha del Multi-Salto en Sistemas Paralelos
Para prescribir una solución efectiva, primero debemos diseccionar la mecánica de fallo del actual "Orquestador Tricameral" de CISRE frente a consultas complejas. La arquitectura actual, basada en la clasificación de intenciones y el lanzamiento de hilos paralelos (Vector + GraphRAG + RAPTOR), representa un patrón de diseño optimizado para la amplitud de la información, pero ciego a la profundidad causal.6
1.1 La Falacia de la Independencia en el Paralelismo
El sistema actual asume implícitamente que los fragmentos de información necesarios para responder una consulta son independientes entre sí o que semánticamente son lo suficientemente cercanos a la consulta original como para ser capturados por una búsqueda vectorial densa.
Consideremos una consulta del dominio de CISRE: "¿Cómo impactan las nuevas regulaciones de seguridad citadas en el anexo B sobre el presupuesto operativo definido en la sección 4?".
Ejecución Actual: El orquestador busca vectores cercanos a "regulaciones seguridad anexo B" y "presupuesto operativo sección 4".
Fallo de Recuperación: Si el "Anexo B" no menciona explícitamente "presupuesto", y la "Sección 4" no menciona "regulaciones", la búsqueda semántica estándar (Cosine Similarity) asignará puntuaciones bajas a los documentos puente que conectan estos conceptos.8 La relación es lógica, no puramente semántica.
Fallo del Grafo: Aunque GraphRAG intente mitigar esto, si la búsqueda es global o local sin una "semilla" correcta (el documento intermedio), el orquestador paralelo simplemente agrega ruido al contexto.
1.2 Latencia vs. Razonamiento: El Dilema del Ingeniero
La solución académica estándar para este problema es el patrón Re-Act (Reason + Act) o Recuperación Iterativa.5 En este modelo, el LLM recibe la consulta, decide buscar X, recibe el resultado, "piensa", y decide buscar Y.
Coste de Latencia: Cada paso de "pensamiento" y generación de nueva consulta añade entre 500ms y 1.5s (dependiendo del modelo y cuantización). Una consulta de 3 saltos () implica un mínimo de 3 llamadas al LLM.
Cálculo de Tiempos:

Para , es matemáticamente imposible mantenerse bajo los 3 segundos si cada ciclo consume ~1.5s.
Por lo tanto, la "Mejor Práctica de Ingeniería" para CISRE no es simplemente adoptar agentes, sino colapsar la latencia de razonamiento moviéndola hacia la pre-computación (Decomposition) y la latencia de recuperación moviéndola hacia el motor de base de datos (SQL Traversal).10
2. Área 1: Descomposición de Consultas (Mejora al Orquestador)
El "Orquestador Tricameral" debe evolucionar de ser un mero "enrutador de tráfico" a un "planificador táctico". La literatura reciente sugiere que descomponer consultas complejas en sub-consultas atómicas antes de la recuperación mejora significativamente la precisión en escenarios RAG.
2.1 Patrón de Diseño: "Decomposition-Map-Reduce"
En lugar de utilizar agentes autónomos que "descubren" el camino, utilizaremos un patrón determinista de Descomposición de Un Solo Disparo (Single-Shot Decomposition). El objetivo es que un modelo de lenguaje muy rápido y ligero analice la consulta inicial y genere un Grafo de Dependencia de Consultas en formato JSON estructurado.
Integración en FastAPI
Actualmente, su endpoint de inferencia probablemente recibe la consulta y la pasa al orquestador. La nueva arquitectura inserta un paso de "Pre-procesamiento Cognitivo".
Entrada: Consulta de usuario.
Modelo de Descomposición: Se invoca un modelo optimizado para latencia (ej. gpt-4o-mini, Claude Haiku, o un modelo local cuantizado 8-bit). La latencia objetivo aquí es <600ms.11
Salida Estructurada: El modelo no responde la pregunta; devuelve una lista de tareas de búsqueda.
Ejecución Híbrida:
Si las sub-consultas son independientes  asyncio.gather (Paralelismo puro, como en el sistema actual).
Si hay dependencia secuencial  Ejecución en cascada optimizada o "Look-ahead Retrieval".
Este enfoque evita la complejidad de gestión de estado de frameworks como LangGraph, manteniendo la simplicidad de un flujo procedimental en Python.12
2.2 Ingeniería de Prompts para Descomposición Ligera
El éxito de esta estrategia depende de la calidad del Prompt del Sistema. Los papers sugieren que la descomposición en "preguntas atómicas" funciona mejor que las instrucciones abstractas de razonamiento.13 Además, el uso de Few-Shot Prompting (aprendizaje con pocos ejemplos) es crucial para forzar al modelo a detectar patrones de multi-salto sin alucinaciones.15
A continuación, se presenta un Prompt de Sistema diseñado específicamente para ser ligero y devolver una estructura parseable (JSON) inmediata, minimizando tokens de salida y tiempo de generación.
Prompt de Sistema Recomendado
SYSTEM PROMPT: QUERY DECOMPOSITION ENGINE
Eres un Arquitecto de Búsqueda para un sistema RAG cognitivo (CISRE). Tu UNICO objetivo es planificar la estrategia de recuperacion de informacion. NO respondas la pregunta del usuario.
TUS INSTRUCCIONES:
Analiza la complejidad de la consulta del usuario.
Si la consulta es simple (pregunta directa), devuelve la consulta original tal cual.
Si la consulta es Multi-Salto (requiere información A para encontrar B), descomponla en pasos atómicos de búsqueda.
Identifica si los pasos pueden ejecutarse en PARALELO (independientes) o requieren SECUENCIA (el paso 2 necesita el output del paso 1).
FORMATO DE SALIDA (JSON Estricto):
{
"is_multihop": boolean,
"execution_mode": "parallel" | "sequential",
"sub_queries": [
{
"id": 1,
"query": "texto de búsqueda optimizado para vector search",
"dependency_id": null (o id del paso previo)
}
]
}
EJEMPLOS FEW-SHOT:
User: "¿Qué es el RAPTOR?"
Assistant: {
"is_multihop": false,
"execution_mode": "parallel",
"sub_queries":
}
User: "Compara las tasas de aprobación entre el distrito escolar de la escuela A y el de la escuela B."
Assistant: {
"is_multihop": true,
"execution_mode": "parallel",
"sub_queries": vs [resultado_2]", "dependency_id": }
]
}
User: "¿Quién es el autor del libro citado en la política de ética del 2023?"
Assistant: {
"is_multihop": true,
"execution_mode": "sequential",
"sub_queries":", "dependency_id": 1}
]
}
Análisis del Prompt:
Atomicidad: Fuerza al modelo a crear strings de búsqueda optimizados para bases de datos vectoriales (eliminando "stop words" o fraseo conversacional).6
Manejo de Dependencias: El uso de marcadores como `` permite a la lógica de Python saber que debe realizar una inyección de contexto dinámica si se elige la ruta secuencial.
Eficiencia: Al pedir JSON estricto, podemos usar el modo json_object de las APIs modernas (OpenAI/Anthropic) para garantizar que no haya parsing errors, reduciendo la necesidad de reintentos y código de limpieza complejo.
3. Área 2: Travesía de Grafo (Mejora a GraphRAG vía SQL)
Esta es la intervención de ingeniería más crítica. La mayoría de las implementaciones de RAG cometen el error de traer datos a la capa de aplicación (Python) para filtrarlos. Dado que CISRE utiliza Supabase (Postgres), tenemos acceso a un motor relacional maduro capaz de realizar joins y recorridos recursivos órdenes de magnitud más rápido que Python.18
3.1 La Ventaja de la Localidad de Datos
En una arquitectura RAG estándar, un salto de 2 niveles implica:
Python  DB: SELECT embedding... (Latencia de Red)
DB  Python: Retorno de 50 chunks (Serialización + Red)
Python: Calcular similitud / buscar enlaces.
Python  DB: SELECT * FROM edges WHERE... (Latencia de Red)
DB  Python: Retorno de vecinos.
Al mover esta lógica a una Función Almacenada (Stored Procedure) o una consulta compleja en Postgres, eliminamos los pasos intermedios de I/O. La base de datos filtra miles de relaciones en memoria y solo devuelve el resultado final refinado. 20 demuestra que para grafos densos, las consultas recursivas (CTE) en SQL superan a las extensiones de grafos dedicadas no optimizadas o la lógica de aplicación en factores de hasta 40x.
3.2 Implementación de "Adjacency Expansion" con pgvector
El objetivo es aprovechar las relaciones explícitas que ya tiene almacenadas (relaciones de grafo, jerarquía RAPTOR). Podemos definir una búsqueda "híbrida" que combine similitud vectorial con travesía de grafos en una sola sentencia SQL.
Estrategia SQL: El CTE de Expansión de Vecindario
En lugar de una simple búsqueda vectorial, implementaremos una consulta que:
Encuentre los "Nodos Ancla" (Top-K por similitud vectorial).
Expanda inmediatamente a sus vecinos directos (1-hop) y secundarios (2-hop) usando la tabla de relaciones.
Devuelva un conjunto consolidado de fragmentos.
Esto permite responder preguntas como "¿Qué conclusiones se derivan del experimento X?" donde el "experimento X" está en un nodo y las "conclusiones" están en nodos hijos o adyacentes, sin necesidad de que el vector de "conclusiones" coincida con la consulta "experimento X".
Consulta SQL Optimizada (Supabase/Postgres)

SQL


-- Función RPC para ser llamada desde Supabase client
CREATE OR REPLACE FUNCTION hybrid_multi_hop_search(
  query_embedding vector(1536),
  match_threshold float,
  limit_count int,
  decay_factor float DEFAULT 0.8
)
RETURNS TABLE (
  chunk_id bigint,
  content text,
  similarity float,
  source_type text, -- 'anchor', '1-hop', '2-hop'
  path_info text
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  WITH RECURSIVE traversal_path AS (
    -- PASO 1: Nodos Ancla (Búsqueda Vectorial Pura)
    SELECT 
      n.id, 
      n.content, 
      1 - (n.embedding <=> query_embedding) as similarity,
      0 as hop_depth,
      'anchor'::text as type,
      n.id::text as path
    FROM nodes n
    WHERE 1 - (n.embedding <=> query_embedding) > match_threshold
    ORDER BY n.embedding <=> query_embedding
    LIMIT limit_count

    UNION ALL

    -- PASO 2: Expansión Recursiva (Travesía de Grafo)
    SELECT 
      child.id,
      child.content,
      -- La similitud del hijo es la del padre atenuada por el decay_factor
      (tp.similarity * decay_factor) as similarity, 
      tp.hop_depth + 1,
      ('hop-' |

| (tp.hop_depth + 1))::text,
      (tp.path |

| '->' |
| child.id)::text
    FROM traversal_path tp
    JOIN edges e ON tp.id = e.source_id -- Asumiendo tabla 'edges'
    JOIN nodes child ON e.target_id = child.id
    WHERE tp.hop_depth < 2 -- Limitar a 2 saltos para latencia controlada
  )
  -- Deduplicación y Selección Final
  SELECT DISTINCT ON (id) 
    id as chunk_id,
    content,
    similarity,
    type as source_type,
    path as path_info
  FROM traversal_path
  ORDER BY id, similarity DESC -- Si un nodo aparece en hop 0 y 1, conservar el de mayor score
  LIMIT limit_count * 3; -- Traer más contexto enriquecido
END;
$$;


Análisis de la Consulta:
WITH RECURSIVE: Es la herramienta clave de Postgres para recorrer estructuras jerárquicas (como árboles RAPTOR) o grafos. Permite iterar sobre los resultados de la consulta anterior.4
Decay Factor: Introducimos un concepto de "decaimiento de relevancia". Si un nodo vecino no se parece semánticamente a la consulta, aún lo traemos porque está conectado a un nodo que sí se parece, pero penalizamos ligeramente su score. Esto ayuda al Gravity Reranking posterior a priorizar.21
Eficiencia: Todo esto ocurre dentro del motor de base de datos. Para un grafo de tamaño moderado (<1M nodos), esta consulta se ejecuta típicamente en <100ms, lo cual es órdenes de magnitud más rápido que hacerlo en Python.22
3.3 Aprovechando Estructuras RAPTOR y Tablas
Dado que su ingesta incluye RAPTOR (resúmenes jerárquicos), esta consulta SQL es doblemente efectiva. Cuando la búsqueda vectorial encuentra un "Resumen de Nivel Superior" (que suele ser más rico semánticamente), la parte recursiva de la consulta extrae automáticamente los nodos hoja (detalles específicos) asociados a ese resumen. Esto resuelve el problema de granularidad: encuentra el concepto general y recupera los detalles específicos en la misma operación de I/O.
4. Análisis Comparativo: Iterative Retrieval vs. Single-Shot Decomposition
El usuario ha solicitado una comparación explícita para decidir la estrategia final. Basándonos en la investigación de latencias de LLMs modernos y la eficiencia de bases de datos, presentamos el siguiente análisis para el contexto de CISRE.
4.1 Definición de los Contendientes
Iterative Retrieval (Re-Act Ligero): El sistema ejecuta un ciclo: Búsqueda  Análisis LLM  Decisión  Nueva Búsqueda. Es el enfoque clásico de agentes "pensantes".
Single-Shot Decomposition (Planificación): El sistema "piensa" una vez al principio para generar todas las consultas necesarias y luego ejecuta una recuperación masiva (posiblemente enriquecida por la travesía de grafo SQL).
4.2 Tabla Comparativa de Ingeniería
Característica
Single-Shot Decomposition (Recomendado)
Iterative Retrieval (Re-Act)
Latencia Típica (3 hops)
Baja (~0.8s - 1.5s). 1 llamada LLM (Plan) + 1 Búsqueda Paralela/SQL.
Alta (>4.0s). 3 llamadas LLM (Secuenciales) + 3 Búsquedas de Red.
Complejidad de Código
Media. Requiere parsing robusto del JSON de planificación. Lógica de gather.
Alta. Gestión de estado, bucles while, manejo de errores en cada iteración, max_steps.
Uso de Tokens (Coste)
Eficiente. El contexto de entrada es solo la consulta.
Costoso. En cada paso se re-envía el contexto acumulado para que el LLM decida el siguiente paso.
Tolerancia a Fallos
Media. Si el plan inicial es malo, la recuperación falla.
Alta. El agente puede "corregir el rumbo" si una búsqueda devuelve vacío.
Sinergia con Supabase
Excelente. Permite lanzar consultas complejas (SQL Recursivo) en una sola transacción.
Pobre. Subutiliza la potencia de SQL al hacer peticiones atómicas simples repetidamente.
Experiencia de Usuario
Fluida. Se siente como una búsqueda estándar.
Lenta. A menudo requiere UI de "streaming" ("Pensando...") para que el usuario no abandone.

4.3 Veredicto: El Enfoque Híbrido "Look-Ahead"
Para cumplir con el requisito de Latencia <3s, el enfoque iterativo puro es inviable con la tecnología de modelos actual (a menos que se usen modelos muy pequeños y estúpidos que fallarían en razonamiento).
La Mejor Práctica para CISRE es una variante híbrida:
Planificación Single-Shot: Usar un modelo rápido para descomponer la intención.
Recuperación "Look-Ahead" en SQL: En lugar de esperar a ver qué devuelve el "Paso 1" para pedir el "Paso 2", usamos la Travesía de Grafo SQL (descrita en la sección 3) para traer el "vecindario" del Paso 1.
Razonamiento: Si la pregunta es "Autor del libro citado en X", la búsqueda vectorial encuentra "X". La travesía SQL de 1-salto trae automáticamente los nodos conectados a "X", uno de los cuales será casi con seguridad el "libro" o el "autor", sin necesidad de que un LLM lo pida explícitamente.
Reranking Agresivo: Dejar que el Gravity Reranking filtre el ruido traído por la expansión del grafo.
5. Implementación Práctica (Pseudocódigo y Patrones)
A continuación, se sintetiza la arquitectura propuesta en un flujo de código Python compatible con su stack actual.
5.1 Estructura del Orquestador (Python/FastAPI)

Python


import asyncio
from typing import List, Optional
from pydantic import BaseModel
# Asumimos clientes instanciados: supabase_client, llm_client

class SubQuery(BaseModel):
    id: int
    query_text: str
    dependency_id: Optional[int] = None

class QueryPlan(BaseModel):
    is_multihop: bool
    sub_queries: List

async def decompose_intent(user_query: str) -> QueryPlan:
    """
    Paso 1: Llamada de baja latencia a gpt-4o-mini o similar.
    Usa el System Prompt definido en la Sección 2.2.
    """
    response = await llm_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[...], # System prompt + user query
        response_format={"type": "json_object"}
    )
    return QueryPlan.model_validate_json(response.choices.message.content)

async def execute_search_strategy(plan: QueryPlan, original_query: str):
    context_chunks =
    
    if not plan.is_multihop:
        # Ruta Rápida: Ejecución paralela existente (Tricameral)
        # Aprovechamos la función SQL mejorada para traer contexto de grafo implícito
        results = await supabase_client.rpc(
            "hybrid_multi_hop_search", 
            {"query_embedding": embed(original_query), "limit_count": 10}
        ).execute()
        context_chunks.extend(results.data)
        
    else:
        # Ruta Multi-Salto: Ejecución de Planificación
        # Optimización: Lanzar todas las queries independientes en paralelo
        independent_queries = [q for q in plan.sub_queries if not q.dependency_id]
        
        # Corrutinas para búsqueda en base de datos (usando la función SQL recursiva)
        tasks = [
            supabase_client.rpc("hybrid_multi_hop_search", {
                "query_embedding": embed(sq.query_text), 
                "limit_count": 5 # Menos chunks por sub-query para no saturar contexto
            }).execute() 
            for sq in independent_queries
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Aplanar resultados
        for res in results:
            context_chunks.extend(res.data)
            
        # Manejo de Dependencias (Simplificado para velocidad)
        # En lugar de un re-act completo, usamos los resultados previos para enriquecer
        # el contexto del reranker, asumiendo que el Graph SQL ya trajo los nodos adyacentes.
        # Si se requiere estrictamente un dato previo (ej. un nombre propio desconocido),
        # se haría una segunda vuelta rápida aquí, pero intentamos evitarlo.

    return context_chunks

async def generate_response(user_query: str):
    # 1. Planificar
    plan = await decompose_intent(user_query)
    
    # 2. Recuperar (SQL Graph-Enhanced)
    raw_chunks = await execute_search_strategy(plan, user_query)
    
    # 3. Gravity Reranking (Crucial para limpiar el ruido del grafo)
    # El reranker debe ver la consulta ORIGINAL y los chunks expandidos
    reranked_chunks = gravity_reranker.rank(query=user_query, documents=raw_chunks)
    
    # 4. Generación Final
    return await llm_client.generate(user_query, context=reranked_chunks[:10])


5.2 Notas sobre la Complejidad del Código
El pseudocódigo anterior demuestra que la complejidad no aumenta exponencialmente. No estamos introduciendo un bucle infinito de agentes. Estamos introduciendo:
Una llamada LLM previa (Decomposition).
Un reemplazo de la función de búsqueda vectorial estándar por una RPC (hybrid_multi_hop_search) que encapsula la complejidad del grafo dentro de Postgres.
Una lógica de orquestación ligeramente más inteligente que sabe manejar una lista de queries en lugar de una sola.
6. Consideraciones de Producción e Integración
6.1 Gestión de Latencia y Presupuesto de Tiempo
Para mantener el SLA de <3s, el desglose aproximado del presupuesto de tiempo debe ser:
Descomposición (LLM Ligero): 400ms - 600ms. (Crítico usar modelos destilados o proveedores rápidos como Groq/Azure OpenAI Low Latency).
Embedding de Sub-Consultas: 100ms. (Paralelizable).
Supabase Graph Search (RPC): 200ms - 400ms. (La parte recursiva añade carga, asegurar índices hnsw en pgvector y índices btree en las claves foráneas de edges).
Gravity Reranking: 300ms - 500ms. (Depende del Cross-Encoder; usar modelos onnx cuantizados en CPU o GPU ligera ayuda).
Generación de Respuesta (Streaming): El primer token debe salir antes de los 3s. El tiempo total de generación no cuenta para la latencia percibida inicial (TTFT).
Total Estimado (TTFT): ~1.5s - 2.0s, dejando un margen de seguridad cómodo.
6.2 Manejo de Errores y "Fallbacks"
El sistema de descomposición puede fallar (ej. JSON malformado o alucinación de dependencias).
Fallback Strategy: Si el paso de descomposición falla o tarda >800ms (timeout), el sistema debe abortar y hacer "fallback" al comportamiento actual (Búsqueda Vectorial Paralela Directa). Esto asegura que el sistema nunca sea peor que la versión actual, solo mejor cuando la complejidad lo permite.
6.3 Indexación en Supabase
Para que la consulta recursiva funcione a velocidad de producción, es imperativo revisar el esquema de base de datos:
Índices: Asegúrese de que edges(source_id) y edges(target_id) tengan índices B-Tree estándar. Un JOIN secuencial en una tabla de relaciones grande matará el rendimiento.
Particionamiento: Si la tabla nodes crece más allá de 1M de filas, considere particionarla, aunque pgvector con índices HNSW escala bien hasta varios millones.
Vacuum: Las tablas de vectores con muchas actualizaciones (ingesta continua) requieren VACUUM agresivo para mantener la velocidad de búsqueda.23
Conclusión
La implementación de Multi-hop Retrieval en CISRE no requiere una reescritura hacia arquitecturas agénticas pesadas. Al aprovechar la inteligencia latente de los modelos de lenguaje modernos para la planificación rápida (Descomposición) y la potencia relacional de PostgreSQL para la ejecución de travesías (Graph SQL), se puede lograr un razonamiento profundo con latencias de búsqueda superficial.
La recomendación final es proceder con la implementación de la función RPC en Supabase y el módulo de descomposición en FastAPI, manteniendo el enfoque "Single-Shot" como estándar y reservando la iteración real solo para casos de borde extremos, fuera del flujo crítico de usuario.
Obras citadas
Postgres as a Graph Database: (Ab)Using PgRouting | Hacker News, fecha de acceso: febrero 12, 2026, https://news.ycombinator.com/item?id=43198520
Beyond Flat Tables: Model Hierarchical Data in Supabase with Recursive Queries, fecha de acceso: febrero 12, 2026, https://dev.to/roel_peters_8b77a70a08fdb/beyond-flat-tables-model-hierarchical-data-in-supabase-with-recursive-queries-4ndl
When Iterative RAG Beats Ideal Evidence: A Diagnostic Study in Scientific Multi-hop Question Answering - arXiv, fecha de acceso: febrero 12, 2026, https://arxiv.org/html/2601.19827
Retrieval-augmented Generation: Part 2 | by Xin Cheng - Medium, fecha de acceso: febrero 12, 2026, https://billtcheng2013.medium.com/retrieval-augmented-generation-part-2-eaf2fdff45dc
RQ-RAG: Learning to Refine Queries for Retrieval Augmented Generation - arXiv, fecha de acceso: febrero 12, 2026, https://arxiv.org/html/2404.00610v1
Implementing RAG using LlamaIndex, Pinecone and Langtrace: A Step-by-Step Guide, fecha de acceso: febrero 12, 2026, https://www.langtrace.ai/blog/implementing-rag-using-llamaindex-pinecone-and-langtrace-a-step-by-step-guide
Iterative RAG Achieves Superior Performance To Gold Context In 11 LLMs, fecha de acceso: febrero 12, 2026, https://quantumzeitgeist.com/performance-iterative-rag-achieves-superior-gold/
NaviX: A Native Vector Index Design for Graph DBMSs With Robust Predicate-Agnostic Search Performance - arXiv, fecha de acceso: febrero 12, 2026, https://arxiv.org/html/2506.23397v1
Comparing Latency of GPT-4o vs. GPT-4o Mini - Workorb Blog, fecha de acceso: febrero 12, 2026, https://www.workorb.com/blog/comparing-latency-of-gpt-4o-vs-gpt-4o-mini
Multi-Agent RAG Framework for Entity Resolution: Advancing Beyond Single-LLM Approaches with Specialized Agent Coordination - MDPI, fecha de acceso: febrero 12, 2026, https://www.mdpi.com/2073-431X/14/12/525
ICML Poster POQD: Performance-Oriented Query Decomposer for Multi-vector retrieval, fecha de acceso: febrero 12, 2026, https://icml.cc/virtual/2025/poster/44047
Exercise 3: RAG with Query Decomposition & Tracing with ... - Medium, fecha de acceso: febrero 12, 2026, https://medium.com/madailab/exercise-3-rag-with-query-decomposition-tracing-with-langsmith-146c140696c1
Zero-Shot, One-Shot, and Few-Shot Prompting, fecha de acceso: febrero 12, 2026, https://learnprompting.org/docs/basics/few_shot
What is few shot prompting? - IBM, fecha de acceso: febrero 12, 2026, https://www.ibm.com/think/topics/few-shot-prompting
Advanced RAG Optimization: Smarter Queries, Superior Insights - Medium, fecha de acceso: febrero 12, 2026, https://medium.com/@myscale/advanced-rag-optimization-smarter-queries-superior-insights-d020a66a8fac
Postgres is all you need, even for vectors - anyblockers, fecha de acceso: febrero 12, 2026, https://anyblockers.com/posts/postgres-is-all-you-need-even-for-vectors
Why are graph traversals faster than joins? Any lessons for normalized data in SQLs?, fecha de acceso: febrero 12, 2026, https://stackoverflow.com/questions/11846131/why-are-graph-traversals-faster-than-joins-any-lessons-for-normalized-data-in-s
PostgreSQL Showdown: Complex Joins vs. Native Graph Traversals with Apache AGE | by Sanjeev Singh | Medium, fecha de acceso: febrero 12, 2026, https://medium.com/@sjksingh/postgresql-showdown-complex-joins-vs-native-graph-traversals-with-apache-age-78d65f2fbdaa
Vector Database Management Techniques and Systems, fecha de acceso: febrero 12, 2026, https://dbgroup.cs.tsinghua.edu.cn/ligl//papers/vdbms-tutorial-clean.pdf
Postgres Vector Search with pgvector: Benchmarks, Costs, and Reality Check - Medium, fecha de acceso: febrero 12, 2026, https://medium.com/@DataCraft-Innovations/postgres-vector-search-with-pgvector-benchmarks-costs-and-reality-check-f839a4d2b66f
pgvector 0.4.0 performance - Supabase, fecha de acceso: febrero 12, 2026, https://supabase.com/blog/pgvector-performance
