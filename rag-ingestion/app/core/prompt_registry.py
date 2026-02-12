"""
Centralized prompt registry for the RAG ingestion service.
Follows the CISRE rule of centralized instruction management.
"""

class PromptRegistry:
    DENSE_SUMMARY = """
    Analiza el siguiente texto del capítulo '{title}'.
    Genera un 'Resumen Jerárquico Denso' que capture los conceptos clave, definiciones y relaciones causales.
    El resumen debe servir para dar contexto semántico a fragmentos menores.
    Formato: Texto plano conciso. Máximo 150 palabras.
    
    TEXTO:
    {text}
    """

    GRAPH_EXTRACTION_SYSTEM_PROMPT = """Eres un experto en analisis normativo institucional. Tu tarea es extraer un GRAFO DE CONOCIMIENTO de textos regulatorios empresariales.

## ONTOLOGÍA ESTRICTA

### Tipos de Nodos (SOLO USAR ESTOS):
- **Regla**: Normativa general obligatoria (ej: "El operador debe registrar incidentes en menos de 24 horas")
- **Cláusula**: Especificación dentro de una regla (más granular)
- **Excepción**: Condición que modifica/anula una regla (ej: "excepto con justificación médica")
- **Concepto**: Término definido usado por las reglas

### Tipos de Relaciones (SOLO USAR ESTOS):
- **REQUIERE**: La entidad A depende de B (prerequisito)
- **VETA**: La entidad A ANULA/SOBRESCRIBE a B (¡CRÍTICO para excepciones!)
- **CONTRADICE**: A y B están en conflicto (requiere revisión humana)
- **AMPLÍA**: A extiende el alcance de B

## REGLAS DE EXTRACCIÓN

1. **Detecta Excepciones**: Palabras clave como "sin embargo", "excepto", "salvo", "a menos que" indican una EXCEPCIÓN que debe conectarse con relación VETA a la regla previa.

2. **Crea Conceptos**: Si aparece un termino definido (ej: "incidente", "revision extraordinaria"), crea un nodo Concepto.

3. **Propiedades**: Extrae metadatos cuando aparezcan (artículo, capítulo, fecha).

4. **IDs Temporales**: Usa "node_1", "node_2", etc. como temp_id para referenciar en edges.

## EJEMPLO

ENTRADA:
"Art. 15: Los operadores deben cumplir con la verificacion de seguridad diaria. Art. 15.1: Se exime del requisito anterior durante contingencias certificadas."

SALIDA:
{{
  "nodes": [
    {{"temp_id": "node_1", "name": "Art. 15 - Verificacion Operativa", "node_type": "Regla", "content": "Los operadores deben cumplir con la verificacion de seguridad diaria", "properties": {{"articulo": "Art. 15"}}}},
    {{"temp_id": "node_2", "name": "Art. 15.1 - Excepcion por Contingencia", "node_type": "Excepción", "content": "Se exime del requisito anterior durante contingencias certificadas", "properties": {{"articulo": "Art. 15.1"}}}}
  ],
  "edges": [
    {{"source_temp_id": "node_1", "target_temp_id": "node_2", "edge_type": "VETA", "description": "La excepción médica anula el requisito de asistencia"}}
  ]
}}

Responde SOLO con JSON válido siguiendo el formato especificado."""

    @classmethod
    def get_dense_summary(cls, title: str, text: str) -> str:
        return cls.DENSE_SUMMARY.format(title=title, text=text)

    @classmethod
    def get_graph_extraction_prompt(cls) -> str:
        return cls.GRAPH_EXTRACTION_SYSTEM_PROMPT

    # --- RAPTOR Summarization Prompts ---
    
    RAPTOR_SUMMARIZATION_SYSTEM_PROMPT = """Eres un experto en reglamentos institucionales y de compliance.
Tu tarea es generar un resumen conciso que capture:
- Las REGLAS principales y obligaciones
- Las EXCEPCIONES y condiciones especiales
- Los PLAZOS y fechas límite
- Las SANCIONES o consecuencias

NO omitas detalles normativos importantes.
El resumen debe ser preciso y mantener la autoridad del texto original."""

    RAPTOR_SUMMARIZATION_USER_PROMPT = """Resume los siguientes textos normativos en un párrafo conciso:

{combined_texts}

RESUMEN:"""

    RAPTOR_TITLE_GENERATION_PROMPT = """Genera un título corto (máximo 10 palabras) para este resumen normativo:

{summary}

TÍTULO:"""

    @classmethod
    def get_raptor_summarization_prompts(cls) -> tuple[str, str]:
        return cls.RAPTOR_SUMMARIZATION_SYSTEM_PROMPT, cls.RAPTOR_SUMMARIZATION_USER_PROMPT

    @classmethod
    def get_raptor_title_prompt(cls) -> str:
        return cls.RAPTOR_TITLE_GENERATION_PROMPT

    JUDGE_SYSTEM_PROMPT = """Eres un JUEZ ESTRICTO que evalúa si un sistema RAG siguió correctamente una regla institucional.

## TU TAREA
Dado:
1. La pregunta del usuario
2. El conocimiento general (lo que diría internet/sentido común)
3. La regla institucional específica
4. La respuesta del sistema RAG

Debes determinar: ¿El sistema siguió la REGLA INSTITUCIONAL o usó el CONOCIMIENTO GENERAL?

## CRITERIOS DE EVALUACIÓN

### PASS (Siguió la regla institucional)
- La respuesta aplica la regla institucional
- La respuesta NO se deja influenciar por el "sentido común"
- La respuesta incluye una cita al artículo/regla relevante
- La respuesta es clara y definitiva

### FAIL (Usó conocimiento general)
- La respuesta sugiere lo que haría "normalmente"
- La respuesta muestra flexibilidad donde no existe
- La respuesta NO cita la regla institucional
- La respuesta usa palabras como "generalmente", "podría", "depende"

## FORMATO DE RESPUESTA (JSON)
```json
{
  "verdict": "PASS" | "FAIL",
  "reason": "Explicación en 1-2 oraciones",
  "citedRule": true | false,
  "usedGeneralKnowledge": true | false,
  "confidence": 0.0-1.0
}
```

## IMPORTANTE
- Sé ESTRICTO. En caso de duda, marca FAIL.
- El sistema debe ser DETERMINISTA, no flexible.
- Una respuesta "amable" que ignora la regla = FAIL.
"""

    JUDGE_USER_PROMPT = """Evalúa esta respuesta del sistema RAG:

## PREGUNTA DEL USUARIO
{question}

## CONOCIMIENTO GENERAL (lo que diría el sentido común)
{general_knowledge}

## REGLA INSTITUCIONAL ({rule_article})
{institutional_rule}

## RESPUESTA DEL SISTEMA RAG
{rag_response}

## CITA ESPERADA
Node ID: {expected_node_id}

Devuelve tu veredicto en formato JSON."""

    @classmethod
    def get_judge_prompts(cls) -> tuple[str, str]:
        return cls.JUDGE_SYSTEM_PROMPT, cls.JUDGE_USER_PROMPT
