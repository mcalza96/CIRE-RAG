"""
Cognitive Container - CIRE-RAG Infrastructure Layer

Centralizes service instantiation and dependency injection.
Prevents static singletons in Domain nodes.
"""

from app.services.knowledge.knowledge_service import KnowledgeService
from app.services.ingestion.pdf_parser import PdfParserService
from app.services.ingestion.router import DocumentStructureRouter
from app.services.ingestion.structure_analyzer import PdfStructureAnalyzer
from app.services.ingestion.toc_discovery import TocDiscoveryService
from app.services.embedding_service import JinaEmbeddingService
from app.core.tools.retrieval import RetrievalTools
from app.application.services.document_download_service import DocumentDownloadService
from app.application.services.ingestion_state_manager import IngestionStateManager
from app.application.services.retrieval_broker import RetrievalBroker
from app.infrastructure.services.storage_service import StorageService
from app.infrastructure.repositories.supabase_source_repository import SupabaseSourceRepository
from app.infrastructure.repositories.supabase_content_repository import SupabaseContentRepository
from app.infrastructure.repositories.supabase_retrieval_repository import SupabaseRetrievalRepository

class CognitiveContainer:
    """
    IoC Container for Cognitive Services.
    """
    _instance = None

    def __init__(self):
        # Lazy initialization of services
        self._knowledge_service = None
        self._pdf_parser_service = None
        self._toc_discovery_service = None
        self._structure_analyzer = None
        self._document_structure_router = None
        self._embedding_service = None
        self._retrieval_repository = None
        self._retrieval_tools = None
        self._storage_service = None
        self._source_repository = None
        self._content_repository = None
        self._download_service = None
        self._state_manager = None
        self._retrieval_broker = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = CognitiveContainer()
        return cls._instance

    @property
    def knowledge_service(self) -> KnowledgeService:
        if self._knowledge_service is None:
            self._knowledge_service = KnowledgeService()
        return self._knowledge_service


    @property
    def pdf_parser_service(self) -> PdfParserService:
        if self._pdf_parser_service is None:
            self._pdf_parser_service = PdfParserService()
        return self._pdf_parser_service

    @property
    def toc_discovery_service(self) -> TocDiscoveryService:
        if self._toc_discovery_service is None:
            self._toc_discovery_service = TocDiscoveryService()
        return self._toc_discovery_service

    @property
    def structure_analyzer(self) -> PdfStructureAnalyzer:
        if self._structure_analyzer is None:
            self._structure_analyzer = PdfStructureAnalyzer()
        return self._structure_analyzer

    @property
    def document_structure_router(self) -> DocumentStructureRouter:
        if self._document_structure_router is None:
            self._document_structure_router = DocumentStructureRouter(analyzer=self.structure_analyzer)
        return self._document_structure_router

    @property
    def embedding_service(self) -> JinaEmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = JinaEmbeddingService.get_instance()
        return self._embedding_service

    @property
    def retrieval_repository(self) -> SupabaseRetrievalRepository:
        if self._retrieval_repository is None:
            self._retrieval_repository = SupabaseRetrievalRepository()
        return self._retrieval_repository

    @property
    def retrieval_tools(self) -> RetrievalTools:
        if self._retrieval_tools is None:
            self._retrieval_tools = RetrievalTools(repository=self.retrieval_repository)
        return self._retrieval_tools
    @property
    def storage_service(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = StorageService()
        return self._storage_service

    @property
    def source_repository(self) -> SupabaseSourceRepository:
        if self._source_repository is None:
            self._source_repository = SupabaseSourceRepository()
        return self._source_repository

    @property
    def content_repository(self) -> SupabaseContentRepository:
        if self._content_repository is None:
            self._content_repository = SupabaseContentRepository()
        return self._content_repository

    @property
    def download_service(self) -> DocumentDownloadService:
        if self._download_service is None:
            self._download_service = DocumentDownloadService(
                storage_service=self.storage_service,
                repository=self.source_repository
            )
        return self._download_service

    @property
    def state_manager(self) -> IngestionStateManager:
        if self._state_manager is None:
            self._state_manager = IngestionStateManager(repository=self.source_repository)
        return self._state_manager

    @property
    def retrieval_broker(self) -> RetrievalBroker:
        if self._retrieval_broker is None:
            self._retrieval_broker = RetrievalBroker(repository=self.retrieval_repository)
        return self._retrieval_broker
