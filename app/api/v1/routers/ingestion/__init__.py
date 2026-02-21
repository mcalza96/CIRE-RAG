from .ingestion_ops import router as ingestion_ops
from .ingestion_batches import router as ingestion_batches
from .ingestion_telemetry import router as ingestion_telemetry
from .ingestion_discovery import router as ingestion_discovery

__all__ = ["ingestion_ops", "ingestion_batches", "ingestion_telemetry", "ingestion_discovery"]
