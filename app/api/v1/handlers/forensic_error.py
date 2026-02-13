from fastapi import Request, status
from fastapi.responses import JSONResponse
from app.core.forensic.stream_validator import ForensicIntegrityError
import logging

logger = logging.getLogger(__name__)

async def forensic_integrity_exception_handler(request: Request, exc: ForensicIntegrityError):
    """
    Handler para errores de integridad forense (alucinaciones detectadas en stream).
    """
    logger.warning(f"Forensic Violation Handled: {exc.attempted_text}")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "PROTOCOL_VIOLATION",
            "message": "Alucinación Detectada: El modelo intentó generar contenido sin respaldo normativo.",
            "attempted_text": exc.attempted_text,
            "missing_proof": exc.missing_proof
        }
    )
