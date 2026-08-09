"""
文件：src/vet_agent/container.py
作用：提供兽医 Agent 项目的业务实现。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from functools import lru_cache

from vet_agent import Settings
from vet_agent import VetOrchestrator
from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluator,
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetyRetriever,
    ClinicalSafetyThresholds,
    FallbackClinicalSafetyRepository,
    FileClinicalSafetyRepository,
    PostgresClinicalSafetyRepository,
)
from vet_agent.repositories import (
    FallbackKnowledgeRepository,
    FileKnowledgeRepository,
    FallbackRuleRepository,
    FileRuleRepository,
    PostgresKnowledgeRepository,
    PostgresRuleRepository,
)
from vet_agent.runtime import QwenClient, QwenEmbeddingClient
from vet_agent.services import (
    AccessControlService,
    ClinicalKnowledgeService,
    JsonAccessControlStore,
    JsonClinicalKnowledgeStore,
    JsonRagGovernanceStore,
    JsonReportStore,
    KnowledgeService,
    LogicTraceStore,
    MemoryService,
    PetContextProvider,
    PostgresAccessControlStore,
    PostgresClinicalKnowledgeStore,
    PostgresLogicTraceStore,
    PostgresMemoryService,
    PostgresRagGovernanceStore,
    PostgresReportStore,
    RagGovernanceService,
    ReportIngestionService,
    make_semantic_memory,
)
from vet_agent.stores import JsonDocumentStore


class Container:
    def __init__(self, settings: Settings) -> None:
        """组装应用运行所需的仓储、模型客户端和业务服务。

        :param settings: 当前运行环境的应用配置。
        :return: 无返回值。
        """
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
        file_clinical_safety_repository = FileClinicalSafetyRepository(settings.clinical_safety_dir)
        clinical_safety_thresholds = ClinicalSafetyThresholds(
            retrieval_min_score=settings.clinical_safety_vector_min_score,
        )
        self.clinical_safety_thresholds = clinical_safety_thresholds
        self.clinical_safety_repository = (
            FallbackClinicalSafetyRepository(
                PostgresClinicalSafetyRepository(settings.database_url),
                file_clinical_safety_repository,
            )
            if settings.database_url
            else file_clinical_safety_repository
        )
        self.clinical_safety_retriever = ClinicalSafetyRetriever(
            self.clinical_safety_repository,
            self.embedding_client,
            thresholds=clinical_safety_thresholds,
        )
        self.clinical_safety_semantic_extractor = ClinicalSafetySemanticExtractorAgent(
            self.qwen_client,
            settings,
        )
        self.clinical_safety_evaluator = ClinicalSafetyEvaluator(
            self.clinical_safety_retriever,
            thresholds=self.clinical_safety_thresholds,
        )
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
        self.clinical_knowledge_service = ClinicalKnowledgeService(
            PostgresClinicalKnowledgeStore(settings.database_url)
            if settings.database_url
            else JsonClinicalKnowledgeStore(JsonDocumentStore(settings.data_dir / "clinical_knowledge.json")),
            embedding_client=self.embedding_client,
        )
        self.orchestrator = VetOrchestrator(
            settings,
            context_provider=PetContextProvider(),
            memory_service=self.memory_service,
            trace_store=self.trace_store,
            knowledge_service=KnowledgeService(self.knowledge_repository),
            qwen_client=self.qwen_client,
            rule_repository=self.rule_repository,
            clinical_safety_evaluator=self.clinical_safety_evaluator,
            clinical_safety_semantic_extractor=self.clinical_safety_semantic_extractor,
        )

    @property
    def ready(self) -> bool:
        """检查模型、规则、知识和临床安全数据是否均可用。

        :return: 所有必要依赖可用时返回 True。
        """
        return (
            self.settings.litellm_configured
            and self.rule_repository.is_ready()
            and self.knowledge_repository.is_ready()
            and self.clinical_safety_repository.is_ready()
        )


@lru_cache
def get_container() -> Container:
    """返回进程级缓存的应用依赖容器。

    :return: 返回使用环境变量构造的依赖容器。
    """
    return Container(Settings.from_env())
