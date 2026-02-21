import math
import structlog
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.infrastructure.supabase.queries.ingestion_query_service import ManualIngestionQueryService

logger = structlog.get_logger(__name__)

class IngestionObservabilityService:
    """
    Extracted service for calculating ingestion progress, stages, 
    and projecting observability metrics for batches and documents.
    """
    
    TERMINAL_SUCCESS_STATES = {"success", "processed", "completed", "ready"}
    TERMINAL_FAILED_STATES = {"failed", "error", "dead_letter"}
    TERMINAL_BATCH_STATES = {"completed", "partial", "failed"}
    
    STAGE_WEIGHTS: Dict[str, float] = {
        "INGEST": 0.15,
        "PERSIST": 0.35,
        "VISUAL": 0.55,
        "RAPTOR": 0.72,
        "GRAPH": 0.88,
        "DONE": 1.0,
        "ERROR": 1.0,
        "OTHER": 0.25,
        "QUEUED": 0.05,
    }

    def __init__(self, query_service: Optional[ManualIngestionQueryService] = None):
        self.query_service = query_service or ManualIngestionQueryService()

    def infer_worker_stage(self, message: str) -> str:
        """Ad-hoc adivinación de etapa basada en el mensaje del worker."""
        text = (message or "").lower()
        if "raptor" in text: return "RAPTOR"
        if "grafo" in text or "graph" in text: return "GRAPH"
        if "visual anchor" in text or "visual" in text: return "VISUAL"
        if "persist" in text: return "PERSIST"
        if "dispatch" in text or "ingestion" in text or "procesamiento" in text: return "INGEST"
        if "error" in text: return "ERROR"
        if "exitoso" in text or "success" in text: return "DONE"
        return "OTHER"

    def is_terminal_doc_status(self, status: str) -> bool:
        normalized = str(status or "").lower().strip()
        return normalized in self.TERMINAL_SUCCESS_STATES or normalized in self.TERMINAL_FAILED_STATES

    def score_for_doc(self, doc: Dict[str, Any]) -> float:
        status = str(doc.get("status") or "").lower().strip()
        if self.is_terminal_doc_status(status):
            return 1.0
        if status in {"queued", "pending", "pending_ingestion"}:
            return self.STAGE_WEIGHTS["QUEUED"]
        
        stage = str(doc.get("worker_stage") or "").strip().upper()
        if not stage:
            stage = "OTHER"
        return float(self.STAGE_WEIGHTS.get(stage, self.STAGE_WEIGHTS["OTHER"]))

    def build_observability_projection(
        self,
        batch: Dict[str, Any],
        docs: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        stage_counts: Dict[str, int],
        queue_snapshot: Dict[str, Optional[int]],
    ) -> Dict[str, Any]:
        """Calcula el porcentaje de progreso y proyecciones de tiempo para un lote."""
        total_files = int(batch.get("total_files") or 0)
        if total_files <= 0:
            total_files = len(docs)

        terminal_docs = 0
        queued_docs = 0
        processing_docs = 0
        score_acc = 0.0

        for doc in docs:
            if not isinstance(doc, dict): continue
            status = str(doc.get("status") or "").lower().strip()
            if self.is_terminal_doc_status(status):
                terminal_docs += 1
            elif status in {"queued", "pending", "pending_ingestion"}:
                queued_docs += 1
            else:
                processing_docs += 1
            score_acc += self.score_for_doc(doc)

        missing_docs = max(0, total_files - len(docs))
        if missing_docs > 0:
            score_acc += missing_docs * self.STAGE_WEIGHTS["QUEUED"]
            queued_docs += missing_docs

        denominator = max(1, total_files)
        progress_percent = round((score_acc / denominator) * 100, 1)
        
        batch_status = str(batch.get("status") or "").lower().strip()
        if batch_status in self.TERMINAL_BATCH_STATES and terminal_docs >= denominator:
            progress_percent = 100.0

        # Dominant stage selection
        dominant_stage = "QUEUED"
        if stage_counts:
            dominant_stage = max(stage_counts.items(), key=lambda item: int(item[1] or 0))[0]
        elif processing_docs > 0:
            dominant_stage = "INGEST"
        elif terminal_docs >= denominator and denominator > 0:
            dominant_stage = "DONE"

        # Time calculations
        created_at_val = batch.get("created_at")
        created_at = self._parse_datetime(created_at_val)
        now = datetime.now(timezone.utc)
        elapsed_seconds = max(0, int((now - created_at).total_seconds())) if created_at else 0

        # ETA logic
        eta_seconds = int(queue_snapshot.get("estimated_wait_seconds") or 0)
        remaining_docs = max(0, denominator - terminal_docs)
        if elapsed_seconds > 0 and terminal_docs > 0 and remaining_docs > 0:
            throughput_docs_per_second = terminal_docs / float(elapsed_seconds)
            if throughput_docs_per_second > 0:
                eta_seconds = int(math.ceil(remaining_docs / throughput_docs_per_second))
        elif remaining_docs <= 0:
            eta_seconds = 0

        # Stall detection
        last_event_at = None
        if events:
            latest = events[0] if isinstance(events[0], dict) else None
            if isinstance(latest, dict):
                last_event_at = str(latest.get("created_at") or "") or None
        
        last_event_dt = self._parse_datetime(last_event_at)
        stalled = False
        if batch_status not in self.TERMINAL_BATCH_STATES and last_event_dt is not None:
            stalled = (now - last_event_dt).total_seconds() > 180

        return {
            "progress_percent": progress_percent,
            "dominant_stage": dominant_stage,
            "eta_seconds": int(max(0, eta_seconds)),
            "stalled": bool(stalled),
            "cursor": self.event_cursor(events),
            "total_files": denominator,
            "terminal_docs": int(terminal_docs),
            "processing_docs": int(processing_docs),
            "queued_docs": int(queued_docs),
            "elapsed_seconds": int(max(0, elapsed_seconds)),
            "last_event_at": last_event_at,
        }

    def event_cursor(self, events: List[Dict[str, Any]]) -> Optional[str]:
        if not events: return None
        for row in events:
            if not isinstance(row, dict): continue
            created_at = str(row.get("created_at") or "").strip()
            event_id = str(row.get("id") or row.get("event_id") or "").strip()
            if created_at and event_id:
                return f"{created_at}|{event_id}"
        return None

    def calculate_visual_accounting(self, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculates detailed metrics for PDF extraction and visual stitching."""
        metrics = {
            "attempted": 0, "stitched": 0, "degraded_inline": 0, 
            "parse_failed": 0, "parse_failed_copyright": 0, "skipped": 0,
            "docs_with_visual": 0, "docs_with_loss": 0, "docs_with_enrichment_pending": 0,
            "docs_searchable_ready": 0, "copyright_refs": []
        }

        for doc in docs:
            metadata = doc.get("metadata", {}) or {}
            
            # 1. Searchable state
            searchable = metadata.get("searchable")
            if isinstance(searchable, dict) and str(searchable.get("status", "")).lower() == "ready":
                metrics["docs_searchable_ready"] += 1

            # 2. Enrichment lag/pending
            enrichment = metadata.get("enrichment", {})
            visual_anchor = metadata.get("visual_anchor", {})
            
            is_pending = False
            if isinstance(enrichment, dict) and str(enrichment.get("status", "")).lower() in {"queued", "already_queued"}:
                is_pending = True
            if isinstance(visual_anchor, dict) and str(visual_anchor.get("status", "")).lower() == "queued":
                is_pending = True
            
            if is_pending:
                metrics["docs_with_enrichment_pending"] += 1

            # 3. Visual Metadata Analysis
            visual = visual_anchor
            if not isinstance(visual, dict) or not visual:
                continue

            metrics["docs_with_visual"] += 1
            
            # Simple aggregations
            metrics["attempted"] += int(visual.get("attempted", 0))
            metrics["stitched"] += int(visual.get("stitched", 0))
            
            # Loss markers
            degraded = int(visual.get("degraded_inline", 0))
            failed = int(visual.get("parse_failed", 0))
            copyright_failed = int(visual.get("parse_failed_copyright", 0))
            skipped = int(visual.get("skipped", 0))

            metrics["degraded_inline"] += degraded
            metrics["parse_failed"] += failed
            metrics["parse_failed_copyright"] += copyright_failed
            metrics["skipped"] += skipped

            if (degraded + failed + skipped) > 0:
                metrics["docs_with_loss"] += 1

            # Copyright References
            refs = visual.get("parse_failed_copyright_refs", [])
            if isinstance(refs, list):
                for r in refs:
                    if not isinstance(r, dict): continue
                    img = str(r.get("image", ""))
                    if "/" in img: img = img.rsplit("/", 1)[-1]
                    metrics["copyright_refs"].append({
                        "doc_id": str(doc.get("id", "")),
                        "filename": str(doc.get("filename", "")),
                        "page": int(r.get("page", 0)),
                        "parent_chunk_id": str(r.get("parent_chunk_id", "")),
                        "image": img
                    })

        metrics["loss_events"] = metrics["degraded_inline"] + metrics["parse_failed"] + metrics["skipped"]
        metrics["copyright_refs_total"] = len(metrics["copyright_refs"])
        metrics["copyright_refs"] = metrics["copyright_refs"][:20] # Cap for UI
        
        return metrics

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value: return None
        try:
            text = str(value).strip()
            if text.endswith("Z"): text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None
