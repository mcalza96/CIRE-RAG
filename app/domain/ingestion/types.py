from enum import Enum

class IngestionStatus(str, Enum):
    PENDING = "pending"
    PENDING_INGESTION = "pending_ingestion" 
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSING_V2 = "processing_v2"
    READY = "ready"
    ERROR = "error"
    EMPTY_FILE = "empty_file"
    SUCCESS = "processed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
