import pytest
from app.core.utils.filename_utils import sanitize_filename

def test_sanitize_filename_with_accents():
    # Basic cases
    assert sanitize_filename("Precálculo.pdf") == "Precalculo.pdf"
    assert sanitize_filename("Ingeniería.docx") == "Ingenieria.docx"
    assert sanitize_filename("Cuestionario de Evaluación.pdf") == "Cuestionario-de-Evaluacion.pdf"

def test_sanitize_filename_with_special_chars():
    assert sanitize_filename("file name with spaces.pdf") == "file-name-with-spaces.pdf"
    assert sanitize_filename("file! @#$%^&*().pdf") == "file-.pdf"
    assert sanitize_filename("multiple---dashes.pdf") == "multiple-dashes.pdf"

def test_sanitize_filename_empty_or_none():
    assert sanitize_filename("") == "unnamed_file"
    assert sanitize_filename(None) == "unnamed_file"

def test_sanitize_filename_preserves_extension():
    assert sanitize_filename("my.file.v1.pdf") == "my.file.v1.pdf"
    assert sanitize_filename(".hiddenfile") == ".hiddenfile"
