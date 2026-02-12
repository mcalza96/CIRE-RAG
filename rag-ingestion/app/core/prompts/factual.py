
import dspy
from typing import List

class FactualSignature(dspy.Signature):
    """
    You are an expert Factual Analyst for the Spanish Government.
    Your task is to answer the User Question based EXCLUSIVELY on the provided Context.
    
    GUIDELINES:
    1. Answer ONLY using the information present in the Context.
    2. If the Context does not contain the answer, say "No tengo información suficiente en el contexto para responder."
    3. Be precise, concise, and direct. Do NOT be Socratic. Do NOT ask follow-up questions.
    4. Cite specific figures, dates, and entity names exactly as they appear in the Context.
    5. Ignore any prior knowledge not present in the Context.
    6. If the User Question contains a FALSE PREMISE that is contradicted by the Context, you MUST CORRECT the user using evidence from the Context (e.g., "El texto no menciona que X sea Y, sino que X es Z").
    """
    
    context = dspy.InputField(desc="The retrieved document chunks containing factual information.")
    question = dspy.InputField(desc="The specific question from the user.")
    
    answer = dspy.OutputField(desc="A direct, factual answer based ONLY on the context.")

class FactualPrompts:
    FALLBACK_SYSTEM_PROMPT = (
        "Eres un Analista de Hechos del Gobierno de España. "
        "Tu tarea es responder a la pregunta del usuario basándote EXCLUSIVAMENTE en el contexto proporcionado.\n"
        "DIRECTRICES:\n"
        "1. Responde SOLO usando la información del Contexto.\n"
        "2. Si el contexto no tiene la respuesta, di 'No tengo información suficiente en el contexto para responder.'.\n"
        "3. Sé preciso, conciso y directo. NO seas socrático.\n"
        "4. Cita cifras y nombres exactamente como aparecen."
    )
    CRITICAL_ERROR = "Error crítico en generación factual."
    NO_QUESTION = "No hay una pregunta específica para procesar."

class FactualModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.generate_answer = dspy.ChainOfThought(FactualSignature)
    
    def forward(self, context: List[str], question: str):
        # Join chunks into a single context string
        context_str = "\n\n".join(context)
        
        prediction = self.generate_answer(
            context=context_str,
            question=question
        )
        
        return prediction
