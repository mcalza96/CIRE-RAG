
from typing import List, Dict, Any, Optional
import pydantic
from app.services.ingestion.toc_discovery import TocEntry, TocResult

class StructureMapper:
    """
    Maps a page number to a hierarchical context based on a discovered Table of Contents.
    """
    def __init__(self, entries: Optional[List[TocEntry]]):
        self.toc_entries = entries if entries else []
        # Sort by start_page ensuring sequential lookup
        self.toc_entries.sort(key=lambda x: x.start_page)

    def map_page_to_context(self, page_number: int) -> Dict[str, Any]:
        """
        Returns a dictionary representing the context of a page.
        """
        if not self.toc_entries:
            return {"structure_context": {}}

        relevant_entries = [
            e for e in self.toc_entries 
            if e.start_page <= page_number and (e.end_page >= page_number if e.end_page else True)
        ]
        
        if not relevant_entries:
            return {"structure_context": {}}
            
        # Sort by level (0=Chapter, 1=Section, etc.)
        chapters = [e for e in relevant_entries if e.level == 0]
        sections = [e for e in relevant_entries if e.level > 0]
        
        details = {}
        if chapters:
            details['chapter'] = chapters[-1].title
        if sections:
            details['section'] = sorted(sections, key=lambda x: x.level)[-1].title
            
        # Build breadcrumbs
        parts = []
        if 'chapter' in details:
            parts.append(details['chapter'])
        if 'section' in details:
            parts.append(details['section'])
            
        if parts:
            details['breadcrumbs'] = " > ".join(parts)
            
        return {"structure_context": details}
