
import re
from typing import List, Optional, BinaryIO
import fitz  # PyMuPDF
from pydantic import BaseModel
import structlog

logger = structlog.get_logger(__name__)

class TocEntry(BaseModel):
    level: int
    title: str
    start_page: int
    end_page: Optional[int] = None

class TocResult(BaseModel):
    has_structure: bool
    entries: List[TocEntry]

class TocDiscoveryService:
    """
    Service to discover the Table of Contents structure from a PDF document.
    """
    
    def discover_toc(self, file_path_or_stream, max_pages_scan: int = 20) -> TocResult:
        """
        Attempts to extract ToC from the document.
        Fails open (returns empty structure) on any error.
        """
        try:
            doc = fitz.open(file_path_or_stream)
            toc: List[List] = doc.get_toc()
            
            # fitz.get_toc() returns list of [lvl, title, page_num]
            # lvl is 1-based hierarchy level.
            # page_num is 1-based page number.
            
            if not toc:
                # Fallback: Try manual regex scan? 
                # For now, rely on PyMuPDF's robust ToC extraction.
                # If PDF has no outline, it returns empty.
                return TocResult(has_structure=False, entries=[])

            entries = []
            for i, item in enumerate(toc):
                level, title, page_num = item
                
                # Determine end_page based on next entry
                next_page = None
                if i + 1 < len(toc):
                    next_page = toc[i+1][2]
                
                # If next entry starts on same page, end_page is same page? 
                # Usually sections end where next begins.
                # If next_page is None (last entry), we don't know end (end of doc).
                
                entries.append(TocEntry(
                    level=level - 1, # Normalize to 0-based
                    title=title.strip(),
                    start_page=page_num,
                    end_page=next_page if next_page else None 
                ))
                
            return TocResult(has_structure=True, entries=entries)

        except Exception as e:
            # Log error but fail open
            logger.error("toc_discovery_failed", error=str(e))
            return TocResult(has_structure=False, entries=[])
