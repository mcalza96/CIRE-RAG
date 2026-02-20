from app.services.ingestion.structure_mapper import StructureMapper
from app.services.ingestion.toc_discovery import TocEntry


def test_map_page_to_context_exposes_section_ref_and_active_entry() -> None:
    mapper = StructureMapper(
        [
            TocEntry(level=0, title="Clause 9", start_page=10, end_page=20),
            TocEntry(level=1, title="9.1 Monitoring", start_page=12, end_page=14),
        ]
    )

    context = mapper.map_page_to_context(13)["structure_context"]

    assert context["breadcrumbs"] == "Clause 9 > 9.1 Monitoring"
    assert context["section_ref"] == "L1:12:9.1 Monitoring"
    active = context["active_toc_entry"]
    assert active["title"] == "9.1 Monitoring"
    assert active["section_ref"] == "L1:12:9.1 Monitoring"
