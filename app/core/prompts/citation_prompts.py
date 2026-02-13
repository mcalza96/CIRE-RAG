"""
Citation Prompts - Prompt templates for guaranteed citations.

These prompts instruct the LLM to emit [[ref:UUID]] tags for every
normative assertion, enabling forensic traceability.
"""
from typing import List, Dict, Optional
from dataclasses import dataclass


# =============================================================================
# SYSTEM PROMPTS
# =============================================================================

CITATION_SYSTEM_PROMPT = """Eres un asistente evaluador institucional con TRAZABILIDAD FORENSE activada.

## REGLA CRÍTICA DE CITACIÓN
Cada vez que apliques una regla, menciones un hecho del contexto, o uses información específica de los documentos proporcionados, DEBES terminar la oración o afirmación con la referencia exacta en formato:

[[ref:NODE_ID]]

Donde NODE_ID es el identificador exacto que aparece al inicio de cada fragmento de contexto.

## EJEMPLOS CORRECTOS:
- "Tu entrega se rechaza por ser posterior a la fecha límite [[ref:abc-123]]."
- "Según el artículo 15, el plagio implica expulsión inmediata [[ref:def-456]]."
- "No se permiten extensiones sin justificación médica [[ref:ghi-789]]."

## PROHIBICIONES:
- NO inventes IDs. Solo usa los que aparecen en el contexto.
- NO omitas citas cuando uses información del contexto.
- NO combines múltiples reglas sin citar cada una.

## FORMATO DE RESPUESTA:
Responde de forma clara y directa, incluyendo las citas inline.
"""

CITATION_SYSTEM_PROMPT_STRICT = """Eres un asistente evaluador institucional con TRAZABILIDAD FORENSE ESTRICTA.

## REGLA ABSOLUTA
CADA afirmación que uses del contexto institucional DEBE incluir su cita.
Formato: [[ref:NODE_ID]] inmediatamente después de la afirmación.

## CONTEXTO PROPORCIONADO
Los fragmentos de contexto vienen en formato:
[NODE_ID]: Contenido del fragmento...

## COMPORTAMIENTO ESPERADO
1. Lee el contexto cuidadosamente
2. Identifica qué reglas aplican al caso
3. Aplica las reglas CON SUS CITAS
4. Si una regla tiene excepciones, cita ambas

## EJEMPLO
Contexto: [rule-001]: Las tareas deben entregarse antes de las 23:59.
Respuesta: "Tu tarea fue rechazada porque el límite era las 23:59 [[ref:rule-001]]."
"""


# =============================================================================
# CONTEXT FORMATTING
# =============================================================================

@dataclass
class ContextChunk:
    """A chunk of context with its metadata."""
    node_id: str
    content: str
    title: Optional[str] = None
    node_type: Optional[str] = None
    authority_level: Optional[str] = None


def format_context_with_ids(
    chunks: List[ContextChunk],
    include_metadata: bool = False
) -> str:
    """
    Format context chunks with their IDs for citation.
    
    Each chunk is formatted as:
    [NODE_ID]: Content...
    
    Args:
        chunks: List of context chunks with IDs.
        include_metadata: Whether to include title/type in output.
        
    Returns:
        Formatted context string.
    """
    formatted_parts = []
    
    for chunk in chunks:
        if include_metadata and chunk.title:
            header = f"[{chunk.node_id}] ({chunk.title}):"
        else:
            header = f"[{chunk.node_id}]:"
        
        formatted_parts.append(f"{header} {chunk.content}")
    
    return "\n\n---\n\n".join(formatted_parts)


def format_nodes_for_citation(
    nodes: List[Dict],
    max_content_length: int = 2000
) -> tuple[str, Dict[str, Dict]]:
    """
    Format regulatory nodes for citation-aware context.
    
    Args:
        nodes: List of node dicts from Supabase.
        max_content_length: Max characters per node content.
        
    Returns:
        Tuple of (formatted_context, node_lookup_dict)
        The lookup dict maps node_id -> node metadata for validation.
    """
    chunks = []
    lookup = {}
    
    for node in nodes:
        node_id = str(node.get("id", ""))
        content = str(node.get("content", ""))[:max_content_length]
        title = node.get("title", "")
        node_type = node.get("node_type", "")
        
        chunks.append(ContextChunk(
            node_id=node_id,
            content=content,
            title=title,
            node_type=node_type
        ))
        
        lookup[node_id] = {
            "id": node_id,
            "title": title,
            "content": content,
            "node_type": node_type,
            "properties": node.get("properties", {})
        }
    
    formatted = format_context_with_ids(chunks, include_metadata=True)
    return formatted, lookup


# =============================================================================
# EVALUATION-SPECIFIC PROMPTS
# =============================================================================

EVALUATION_WITH_CITATIONS_PROMPT = """Evalúa el siguiente trabajo del estudiante.

## CONTEXTO INSTITUCIONAL (CITA OBLIGATORIA)
{context}

## TRABAJO DEL ESTUDIANTE
{submission}

## INSTRUCCIONES
1. Aplica las reglas del contexto institucional
2. CADA regla que apliques debe incluir su cita [[ref:NODE_ID]]
3. Proporciona retroalimentación constructiva
4. Indica claramente la calificación y justificación

## RESPUESTA (con citas obligatorias):
"""

DECISION_WITH_CITATIONS_PROMPT = """Toma una decisión sobre la siguiente solicitud.

## NORMATIVA APLICABLE (CITA OBLIGATORIA)
{context}

## SOLICITUD
{request}

## INSTRUCCIONES
1. Identifica qué reglas aplican
2. Aplica cada regla CON SU CITA [[ref:NODE_ID]]
3. Si hay excepciones, menciónalas con sus citas
4. Da una decisión clara y fundamentada

## DECISIÓN (con citas obligatorias):
"""


# =============================================================================
# PHASE 6: CITATIONS AS CURRENCY (ALIGMENT ENFORCEMENT)
# =============================================================================

CITATIONS_AS_CURRENCY_PROMPT = """Eres un asistente de alta fidelidad para CIRE-RAG con PROTOCOLO DE INTEGRIDAD FORENSE (Fase 6).

## REGLA DE ORO: EL SILENCIO ES PREFERIBLE A LA ALUCINACIÓN
No tienes permiso para generar una sola oracion que no este respaldada por evidencia documental y normativa.

## PROTOCOLO "CITAS COMO MONEDA"
Cada afirmación, dato o explicación que generes DEBE "pagar" su existencia con dos fichas de trazabilidad:
1. Origen Documental (RAPTOR_ID): El nodo del material fuente.
2. Permiso Legal (GRAPH_RULE_UUID): La regla institucional que valida la afirmación.

## FORMATO OBLIGATORIO
Al final de CADA oración (antes del punto final), inserta el tag de cita:
"Texto de la afirmación <cite source='[ID_ACADEMICO]' rule='[ID_REGLA]' />."

## RESTRICCIONES CRÍTICAS:
- Si no encuentras una fuente académica (RAPTOR) o una regla (Graph), NO generes el texto.
- <cite> es un tag técnico, no lo ocultes ni lo modifiques.
- Cita CADA oración por separado. No acepto una cita al final de un párrafo para cubrir múltiples oraciones.
- Los IDs deben ser exactos a los proporcionados en el contexto.

## EJEMPLO:
Contexto Documental: [node_policy_01]: Las incidencias criticas deben escalarse en menos de 60 minutos.
Contexto Normativo: [rule_ops_101]: Todo escalamiento requiere evidencia trazable.

Respuesta: "Las incidencias criticas deben escalarse en menos de 60 minutos <cite source='node_policy_01' rule='rule_ops_101' />. El escalamiento debe incluir evidencia trazable del evento <cite source='node_policy_01' rule='rule_ops_101' />."
"""
