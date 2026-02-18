import re
import unicodedata
import os

def sanitize_filename(filename: str) -> str:
    """
    Sanitiza un nombre de archivo para ser usado de forma segura en Supabase Storage.
    - Normaliza Unicode (NFD -> NFC).
    - Convierte a ASCII si es posible (acentos).
    - Reemplaza espacios por guiones.
    - Elimina caracteres no alfanuméricos (excepto . y -).
    """
    if not filename:
        return "unnamed_file"
    
    # Normalizar a NFC (acentos combinados -> caracteres únicos)
    filename = unicodedata.normalize('NFC', filename)
    
    # Intentar convertir a ASCII (eliminar acentos)
    # Ejemplo: 'á' -> 'a'
    filename = "".join(
        c for c in unicodedata.normalize('NFD', filename)
        if unicodedata.category(c) != 'Mn'
    )
    
    # Reemplazar caracteres no deseados preservando puntos y guiones
    # No usamos os.path.splitext para evitar problemas con múltiples puntos o archivos ocultos
    # simplemente limpiamos lo que sea ilegal en storage.
    filename = re.sub(r'[^a-zA-Z0-9._\-]', '-', filename)
    # Colapsar guiones repetidos
    filename = re.sub(r'-+', '-', filename)
    # Quitar guiones al inicio o final (pero no puntos)
    filename = filename.strip('-')
    
    return filename or "unnamed_file"
