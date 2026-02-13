"""
DSPy Socratic Prompt Architecture
=================================

This module defines guided reasoning logic using Stanford's DSPy framework.
It replaces the legacy string-based prompt engineering with optimizable Signatures and Modules.
Now includes Phase 5: Active Guardrails (DSPy Assertions).
"""

import dspy
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class SocraticSignature(dspy.Signature):
    """
    Actua como un asistente de analisis que guia al usuario mediante preguntas sin entregar respuestas no verificadas.
    """
    context: str = dspy.InputField(
        desc="Fragmentos de conocimiento recuperados relevantes para la consulta."
    )
    user_question: str = dspy.InputField(
        desc="La pregunta o solicitud actual del usuario."
    )
    
    quality_reflection: str = dspy.OutputField(
        desc="Evaluacion interna sobre brechas de informacion detectadas y estrategia de respuesta grounded."
    )
    socratic_response: str = dspy.OutputField(
        desc="La respuesta final al usuario. CADA afirmacion basada en contexto DEBE incluir cita [[cita:UUID]]."
    )

def validate_socratic_response(context: str, question: str, response: str) -> Tuple[bool, str]:
    """
    Heuristic Validator for Socratic Responses (Guardrails).
    Returns (is_valid, feedback_msg).
    """
    # 1. Check Length (< 10 words is too short, > 100 is too long lecture)
    words = len(response.split())
    if words > 100:
        return False, "La respuesta es demasiado larga. Sé más conciso y socrático (menos de 100 palabras)."
    if words < 5:
        return False, "La respuesta es demasiado corta. Elabora una guía útil."

    # 2. Check for Question Mark (Must end with ?)
    if not response.strip().endswith("?"):
        return False, "Tu respuesta DEBE cerrar con una pregunta de validacion para el usuario."

    # 3. Check for Direct Answers (Naive but fast check)
    # Ideally use an LLM call here, but heuristics save latency.
    forbidden = ["la respuesta es", "es decir", "significa que"]
    lower_resp = response.lower()
    for phrase in forbidden:
        if phrase in lower_resp:
            return False, f"Evita dar la respuesta directa ('{phrase}'). Guía con preguntas."

    return True, "OK"

class SocraticModule(dspy.Module):
    """
    Chain-of-Thought reasoning module with Active Guardrails (Suggestions).
    """
    def __init__(self):
        super().__init__()
        # ChainOfThought introduces a "reasoning" trace before the final answer
        # allowing the model to "think" about the strategy.
        self.predict = dspy.ChainOfThought(SocraticSignature)
        
    def forward(self, context: str, user_question: str):
        # 1. Run Prediction
        pred = self.predict(
            context=context,
            user_question=user_question
        )
        
        # 2. Apply Guardrails (Assertions/Suggestions)
        # dspy.Suggest allows backtracking if condition is False
        is_valid, feedback = validate_socratic_response(context, user_question, pred.socratic_response)
        
        dspy.Suggest(
            is_valid,
            f"Error de calidad detectado: {feedback}. Reformula la respuesta para cumplir la regla."
        )
        
        return pred

class SocraticPrompts:
    """
    Centralized repository for Socratic Workflow prompts.
    """
    
    TRIAGE_SYSTEM_PROMPT = """You are the CISRE Intent Router and Diagnostic Engine.
    Classify the user's latest input into the correct Intent category with high precision.
    INTENTS: FACT_QUERY, ACTION_REQUEST, CLARIFICATION, CHIT_CHAT, OFF_TOPIC."""

    EVALUATION_SYSTEM_PROMPT = """You are the 'Silent Judge' of a grounded assistant.
    Evaluate the user's message based on the provided CONTEXT.
    Analyze: Logic, Terminology, Misconceptions."""

    REFLECTION_SYSTEM_PROMPT = """You are the Retrieval Quality Auditor.
    Compare the user's answer to the CONTEXT provided.
    Estimate 'depth_level' (1-5) honestly.
    """

    CHAT_FALLBACK_SYSTEM_PROMPT = "Eres un asistente de analisis. Responde al usuario basandote en el contexto proporcionado y guiando con preguntas de precision."

    SPECULATIVE_SYSTEM_PROMPT = """Eres un acelerador cognitivo. Mientras el sistema busca datos precisos, tu tarea es generar
una 'Estrategia de Respuesta Provisional' basada en el historial del chat.
Si el usuario saluda, responde al saludo.
Si pregunta algo complejo, formula una pregunta socrática aclaratoria genérica.
NO intentes inventar hechos. Céntrate en la estructura de la conversación."""

    @staticmethod
    def render_speculation_seed(draft: str) -> str:
        return (
            f"\n\n[SPECULATIVE DRAFT (Speed Optimization)]\n"
            f"Se ha generado un borrador preliminar: '{draft}'.\n"
            f"Si el borrador es coherente con el contexto recuperado (RAG), ÚSALO como base y refínalo.\n"
            f"Si el contexto recuperado contradice el borrador o trae información nueva crucial, IGNORA el borrador."
        )

    GREETING = "Hola, ¿en qué puedo ayudarte?"
    CRITICAL_ERROR = "Lo siento, tuve un problema interno crítico."
    NO_QUESTION = "No se ha detectado una pregunta específica."
