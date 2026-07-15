from __future__ import annotations

from functools import lru_cache

from src.vet_agent.config import Settings
from src.vet_agent.orchestrator import VetOrchestrator
from src.vet_agent.runtime.qwen import QwenClient
from src.vet_agent.repositories.knowledge import (
    FallbackKnowledgeRepository,
    FileKnowledgeRepository,
    PostgresKnowledgeRepository,
)
from src.vet_agent.repositories.rules import FallbackRuleRepository, FileRuleRepository, PostgresRuleRepository
from src.vet_agent.runtime.embeddings import QwenEmbeddingClient
from src.vet_agent.services.context import PetContextProvider
from src.vet_agent.services.access_control import (
    AccessControlService,
    JsonAccessControlStore,
    PostgresAccessControlStore,
)
from src.vet_agent.services.knowledge import KnowledgeService
from src.vet_agent.services.memory import MemoryService
from src.vet_agent.services.postgres_memory import PostgresMemoryService
from src.vet_agent.services.postgres_trace import PostgresLogicTraceStore
from src.vet_agent.services.semantic_memory import make_semantic_memory
from src.vet_agent.services.trace import LogicTraceStore
from src.vet_agent.stores.json_store import JsonDocumentStore


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.semantic_memory = make_semantic_memory(
            enabled=settings.enable_mem0,
            api_key=settings.mem0_api_key,
        )
        self.memory_service = (
            PostgresMemoryService(settings.database_url, semantic_memory=self.semantic_memory)
            if settings.database_url
            else MemoryService(JsonDocumentStore(settings.data_dir / "memory.json"))
        )
        self.access_control = AccessControlService(
            settings,
            PostgresAccessControlStore(settings.database_url)
            if settings.database_url
            else JsonAccessControlStore(JsonDocumentStore(settings.data_dir / "access_control.json")),
        )
        self.trace_store = (
            PostgresLogicTraceStore(settings.database_url)
            if settings.database_url
            else LogicTraceStore(JsonDocumentStore(settings.data_dir / "logic_trace.jsonl"))
        )
        self.qwen_client = QwenClient(settings)
        self.embedding_client = (
            QwenEmbeddingClient(settings)
            if settings.enable_rag_embeddings and settings.qwen_configured
            else None
        )
        file_rule_repository = FileRuleRepository(settings.seed_dir)
        file_knowledge_repository = FileKnowledgeRepository(settings.seed_dir)
        self.rule_repository = (
            FallbackRuleRepository(PostgresRuleRepository(settings.database_url), file_rule_repository)
            if settings.database_url
            else file_rule_repository
        )
        self.knowledge_repository = (
            FallbackKnowledgeRepository(
                PostgresKnowledgeRepository(settings.database_url, self.embedding_client),
                file_knowledge_repository,
            )
            if settings.database_url
            else file_knowledge_repository
        )
        self.orchestrator = VetOrchestrator(
            settings,
            context_provider=PetContextProvider(),
            memory_service=self.memory_service,
            trace_store=self.trace_store,
            knowledge_service=KnowledgeService(self.knowledge_repository),
            qwen_client=self.qwen_client,
            rule_repository=self.rule_repository,
        )

    @property
    def ready(self) -> bool:
        return (
            (self.settings.qwen_configured or self.settings.allow_mock_llm)
            and self.rule_repository.is_ready()
            and self.knowledge_repository.is_ready()
        )


@lru_cache
def get_container() -> Container:
    return Container(Settings.from_env())
