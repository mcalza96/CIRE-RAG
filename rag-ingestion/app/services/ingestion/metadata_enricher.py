import re
from typing import Dict, Any, Tuple
import structlog

logger = structlog.get_logger(__name__)

class MetadataEnricher:
    """
    Service to enrich chunk metadata with deterministic regex extraction.
    Identifies structural entities like exercises, theorems, definitions, sections, and figures.
    """
    
    # Pre-compiled regex patterns for performance
    PATTERNS = {
        "exercise_id": re.compile(r"(?:Ejercicio|Problema|Actividad)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
        "theorem_id": re.compile(r"(?:Teorema|Lema|Corolario|Proposición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
        "definition_id": re.compile(r"(?:Definición)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
        "example_id": re.compile(r"(?:Ejemplo)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
        "section_id": re.compile(r"(?:Sección)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
        "figure_id": re.compile(r"(?:Figura|Imagen|Gráfico)\s+(\d+(?:\.\d+)?)", re.IGNORECASE),
    }

    def enrich(self, text: str, current_metadata: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Scans text for patterns and injects found entities into metadata.
        Returns the (potentially modified) text and the updated metadata.
        """
        updates = {}
        
        # We might want to prepend tags to the text to help the LLM context, 
        # but let's keep it clean for now and just update metadata.
        # If user requested text modification:
        # "TIP: Añadir también al comienzo del texto del chunk ayuda al LLM"
        
        found_tags = []

        for key, pattern in self.PATTERNS.items():
            # Find all matches (or just the first? Usually specific chunks have one main ID, 
            # but could reference others. Let's capture the FIRST distinct one as the 'primary' ID
            # or maybe a list if multiple?
            # For simplicity and RAG filtering, let's store the FIRST match as the primary identifier.
            match = pattern.search(text)
            if match:
                value = match.group(1)
                updates[key] = value
                found_tags.append(f"[{key.upper()}: {value}]")

        # Merge updates
        new_metadata = current_metadata.copy()
        new_metadata.update(updates)
        
        # Optional: Prepend tags to text if not already present
        # This helps the LLM see "EXERCISE_ID: 15" clearly even if the text is "15. Solve x..."
        prefix = " ".join(found_tags)
        if prefix:
            # Check if text already starts with it to avoid duplication (idempotency)
            if not text.startswith("["): 
                 new_text = f"{prefix}\n{text}"
            else:
                 new_text = text
        else:
            new_text = text

        return new_text, new_metadata
