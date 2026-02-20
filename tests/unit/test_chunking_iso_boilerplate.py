from app.services.ingestion.chunking_service import ChunkingService


def test_strip_iso_boilerplate_removes_known_header_footer() -> None:
    raw = (
        "NORMA INTERNACIONAL Traduccion oficial Official translation Traduction officielle\n"
        "Requisitos del sistema\n"
        "ISO 9001:2015 (traduccion oficial) texto legal © ISO 2015 - Todos los derechos reservados"
    )
    cleaned = ChunkingService._strip_iso_boilerplate(raw)

    assert "NORMA INTERNACIONAL" not in cleaned
    assert "Official translation" not in cleaned
    assert "Todos los derechos reservados" not in cleaned
    assert "Requisitos del sistema" in cleaned
