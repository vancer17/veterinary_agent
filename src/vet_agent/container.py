from __future__ import annotations

from functools import lru_cache

from vet_agent.config import Settings
from vet_agent.orchestrator import VetOrchestrator
from vet_agent.runtime.qwen import QwenClient
from vet_agent.repositories.knowledge import (
    FallbackKnowledgeRepository,
    FileKnowledgeRepository,
    PostgresKnowledgeRepository,
)
from vet_agent.repositories.rules import FallbackRuleRepository, FileRuleRepository, PostgresRuleRepository
from vet_agent.runtime.embeddings import QwenEmbeddingClient
from vet_agent.services.context import PetContextProvider
from vet_agent.services.access_control import (
    AccessControlService,
    JsonAccessControlStore,
    PostgresAccessControlStore,
)
from vet_agent.services.knowledge import KnowledgeService
from vet_agent.services.memory import MemoryService
from vet_agent.services.postgres_memory import PostgresMemoryService
from vet_agent.services.postgres_trace import PostgresLogicTraceStore
from vet_agent.services.rag_governance import (
    JsonRagGovernanceStore,
    PostgresRagGovernanceStore,
    RagGovernanceService,
)
from vet_agent.services.reports import JsonReportStore, PostgresReportStore, ReportIngestionService
from vet_agent.services.semantic_memory import make_semantic_memory
from vet_agent.services.trace import LogicTraceStore
from vet_agent.stores.json_store import JsonDocumentStore


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.semantic_memory = make_semantic_memory(settings)
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
            if settings.enable_rag_embeddings and settings.litellm_configured
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
        self.report_service = ReportIngestionService(
            PostgresReportStore(settings.database_url)
            if settings.database_url
            else JsonReportStore(JsonDocumentStore(settings.data_dir / "reports.json")),
            self.qwen_client,
            settings,
        )
        self.rag_governance_service = RagGovernanceService(
            PostgresRagGovernanceStore(settings.database_url)
            if settings.database_url
            else JsonRagGovernanceStore(settings.seed_dir, JsonDocumentStore(settings.data_dir / "rag_governance.json"))
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
            self.settings.litellm_configured
            and self.rule_repository.is_ready()
            and self.knowledge_repository.is_ready()
        )


@lru_cache
def get_container() -> Container:
    return Container(Settings.from_env())
