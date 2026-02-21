from typing import Optional

class ForensicIntegrityError(Exception):
    """
    Excepción lanzada cuando se detecta una violación de integridad forense.
    """
    def __init__(
        self, 
        message: str, 
        attempted_text: str, 
        missing_proof: Optional[str] = None
    ):
        super().__init__(message)
        self.message = message
        self.attempted_text = attempted_text
        self.missing_proof = missing_proof
