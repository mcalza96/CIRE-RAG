from typing import Annotated
from fastapi import Depends, Request
from app.infrastructure.container import CognitiveContainer

def get_container(request: Request) -> CognitiveContainer:
    """
    Dependency injection for the CognitiveContainer.
    Pulls the singleton instance from the app state (initialized in lifespan).
    """
    return request.app.state.container

def get_retrieval_broker(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.retrieval_broker

def get_knowledge_service(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.knowledge_service

def get_atomic_engine(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.atomic_engine

def get_storage_service(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.storage_service

def get_source_repository(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.source_repository

def get_content_repository(container: Annotated[CognitiveContainer, Depends(get_container)]):
    return container.content_repository
