"""
文件：src/vet_agent/container.py
作用：组装兽医 Agent 运行所需的仓储、范围上下文服务、模型客户端与业务服务。
范围：作为应用依赖注入入口，负责将 PostgreSQL 范围仓储和 turn execution 门禁接入主数据链。
说明：身份、宠物资料与会话范围、幂等与 turn lock 不再提供 JSON 回退；未显式注入测试替身且缺少 DATABASE_URL 时按 Fail Fast 处理。
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
from vet_agent.input_safety import (
    GuardrailsInputSafetyDetector,
    InputSafetyPolicyClient,
    InputSafetyRepository,
    InputSafetyService,
    LocalInputSafetyPolicyClient,
    OpaInputSafetyPolicyClient,
    PostgresInputSafetyRepository,
)
from vet_agent.repositories import (
    FallbackKnowledgeRepository,
    FileKnowledgeRepository,
    FallbackRuleRepository,
    FileRuleRepository,
    PostgresKnowledgeRepository,
    PostgresRuleRepository,
    PostgresScopeRepository,
    PostgresTurnExecutionRepository,
    ScopeRepository,
)
from vet_agent.runtime import QwenClient, QwenEmbeddingClient
from vet_agent.services import (
    AccessControlService,
    ClinicalKnowledgeService,
    JsonClinicalKnowledgeStore,
    JsonRagGovernanceStore,
    JsonReportStore,
    KnowledgeService,
    LogicTraceStore,
    MemoryService,
    PetContextProvider,
    PostgresClinicalKnowledgeStore,
    PostgresLogicTraceStore,
    PostgresMemoryService,
    PostgresRagGovernanceStore,
    PostgresReportStore,
    RagGovernanceService,
    ReportIngestionService,
    ScopeContextService,
    TurnExecutionGate,
    TurnExecutionGateProtocol,
    make_semantic_memory,
)
from vet_agent.stores import JsonDocumentStore


_container_override: Container | None = None


class Container:
    def __init__(
        self,
        settings: Settings,
        *,
        scope_repository: ScopeRepository | None = None,
        turn_execution_gate: TurnExecutionGateProtocol | None = None,
        input_safety_service: InputSafetyService | None = None,
    ) -> None:
        """组装应用运行所需的仓储、模型客户端和业务服务。

        :param settings: 当前运行环境的应用配置。
        :param scope_repository: 身份、宠物资料与会话范围仓储；仅测试或特殊嵌入场景可显式注入。
        :param turn_execution_gate: 单回合执行门禁；仅测试或特殊嵌入场景可显式注入。
        :param input_safety_service: 基础输入安全服务；仅测试或特殊嵌入场景可显式注入。
        :return: 无返回值。
        """
        self.settings = settings
        self.scope_repository = self._scope_repository(settings, scope_repository)
        self.scope_service = ScopeContextService(self.scope_repository)
        self.turn_execution_gate = self._turn_execution_gate(settings, turn_execution_gate)
        self.semantic_memory = make_semantic_memory(settings)
        self.memory_service = (
            PostgresMemoryService(settings.database_url, semantic_memory=self.semantic_memory)
            if settings.database_url
            else MemoryService(JsonDocumentStore(settings.data_dir / "memory.json"))
        )
        self.access_control = AccessControlService(settings, self.scope_service)
        self.input_safety_service = self._input_safety_service(settings, input_safety_service)
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
            context_provider=PetContextProvider(self.scope_service),
            memory_service=self.memory_service,
            trace_store=self.trace_store,
            knowledge_service=KnowledgeService(self.knowledge_repository),
            qwen_client=self.qwen_client,
            rule_repository=self.rule_repository,
            clinical_safety_evaluator=self.clinical_safety_evaluator,
            clinical_safety_semantic_extractor=self.clinical_safety_semantic_extractor,
            turn_execution_gate=self.turn_execution_gate,
            input_safety_service=self.input_safety_service,
        )

    @property
    def ready(self) -> bool:
        """检查模型、规则、知识和临床安全数据是否均可用。

        :return: 所有必要依赖可用时返回 True。
        """
        return (
            self.settings.litellm_configured
            and self.access_control.is_ready()
            and self.input_safety_service.is_ready()
            and self.rule_repository.is_ready()
            and self.knowledge_repository.is_ready()
            and self.clinical_safety_repository.is_ready()
            and self.turn_execution_gate.is_ready()
        )

    def _scope_repository(
        self,
        settings: Settings,
        scope_repository: ScopeRepository | None,
    ) -> ScopeRepository:
        """构造身份、宠物资料与会话范围仓储。

        :param settings: 当前运行环境的应用配置。
        :param scope_repository: 外部显式注入的范围仓储。
        :return: 返回范围仓储实例。
        """
        if scope_repository is not None:
            return scope_repository
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for identity, pet profile and session scope")
        return PostgresScopeRepository(settings.database_url)

    def _turn_execution_gate(
        self,
        settings: Settings,
        turn_execution_gate: TurnExecutionGateProtocol | None,
    ) -> TurnExecutionGateProtocol:
        """构造单回合执行门禁。

        :param settings: 当前运行环境的应用配置。
        :param turn_execution_gate: 外部显式注入的单回合执行门禁。
        :return: 返回单回合执行门禁实例。
        """
        if turn_execution_gate is not None:
            return turn_execution_gate
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for turn execution gate")
        return TurnExecutionGate(settings, PostgresTurnExecutionRepository(settings.database_url))

    def _input_safety_service(
        self,
        settings: Settings,
        input_safety_service: InputSafetyService | None,
    ) -> InputSafetyService:
        """构造基础输入安全候选与策略裁决服务。

        :param settings: 当前运行环境的应用配置。
        :param input_safety_service: 外部显式注入的输入安全服务。
        :return: 返回输入安全服务实例。
        """
        if input_safety_service is not None:
            return input_safety_service
        if not settings.enable_input_safety:
            raise RuntimeError("ENABLE_INPUT_SAFETY=false is only allowed through explicit test injection")
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for input safety candidate definitions")
        repository = PostgresInputSafetyRepository(settings.database_url)
        detectors = (
            (
                GuardrailsInputSafetyDetector(
                    settings,
                    repository,
                    system_prompt="兽医 Agent 输入安全检测器仅采集提示注入候选，最终动作由 OPA 裁决。",
                ),
            )
            if settings.enable_input_safety_guardrails
            else ()
        )
        return InputSafetyService(
            settings,
            repository=repository,
            detectors=detectors,
            policy_client=self._input_safety_policy_client(settings),
        )

    def _input_safety_policy_client(self, settings: Settings) -> InputSafetyPolicyClient:
        """构造基础输入安全策略裁决客户端。

        :param settings: 当前运行环境的应用配置。
        :return: 返回输入安全策略客户端。
        """
        if settings.input_safety_policy_backend == "opa":
            return OpaInputSafetyPolicyClient(
                base_url=settings.input_safety_opa_base_url,
                version="v1",
                package_path=settings.input_safety_opa_package_path,
                rule_name=settings.input_safety_opa_rule_name,
                auth_token=settings.input_safety_opa_auth_token,
                timeout_seconds=settings.request_timeout_seconds,
            )
        if settings.input_safety_policy_backend == "local":
            return LocalInputSafetyPolicyClient()
        raise RuntimeError(f"unsupported INPUT_SAFETY_POLICY_BACKEND: {settings.input_safety_policy_backend}")


@lru_cache
def get_container() -> Container:
    """返回进程级缓存的应用依赖容器。

    :return: 返回使用环境变量构造的依赖容器。
    """
    if _container_override is not None:
        return _container_override
    return Container(Settings.from_env())


def set_container(container: Container | None) -> None:
    """设置或清理进程级容器覆盖对象。

    :param container: 显式注入的容器，传入 None 时清理覆盖对象并恢复默认构造。
    :return: 无返回值。
    """
    global _container_override
    _container_override = container
    get_container.cache_clear()
