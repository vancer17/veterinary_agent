##################################################################################################
# 文件: src/veterinary_agent/app/bootstrap.py
# 作用: 定义应用组合根，集中解析运行配置、创建组件实例并构建 FastAPI 应用状态。
# 边界: 只负责依赖装配和默认 TODO 空壳选择；不管理 ASGI 生命周期、不处理 HTTP 请求、不执行领域业务。
##################################################################################################

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from veterinary_agent.agent_application_service import (
    AgentApplicationService,
    AgentGraphRuntime,
    AgentLogicTraceStore,
    DefaultAgentApplicationService,
    LogicTraceAgentTraceStore,
    TodoAgentGraphRuntime,
)
from veterinary_agent.agent_runner import (
    AgentRunner,
    AgentRunnerTraceSink,
    LogicTraceAgentRunnerTraceSink,
    create_default_agent_runner,
)
from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)
from veterinary_agent.checkpoint_store import (
    LangGraphPostgresSaverProvider,
    load_langgraph_postgres_saver_settings,
)
from veterinary_agent.config import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    ConversationStoreSettings,
    LlmGatewaySettings,
    ObservabilitySettings,
    RuntimeConfigProvider,
    RuntimeConfigSettings,
    create_runtime_config_provider,
    load_api_ingress_settings,
    load_checkpoint_store_settings,
    load_conversation_store_settings,
    load_llm_gateway_settings,
)
from veterinary_agent.conversation_store import ConversationStore, TodoConversationStore
from veterinary_agent.llm_gateway import (
    LlmCallTraceStore,
    LlmGateway,
    LogicTraceLlmCallTraceStore,
    create_default_llm_gateway,
)
from veterinary_agent.logic_trace_store import LogicTraceStore, TodoLogicTraceStore
from veterinary_agent.observability import (
    ObservabilityProvider,
    create_observability_provider,
)
from veterinary_agent.pet_session_policy import (
    DefaultPetSessionPolicy,
    LogicTracePetSessionTraceSink,
    PetSessionPolicy,
)


CheckpointProviderFactory = Callable[[], CheckpointProviderLifecycle]
ConversationStoreFactory = Callable[[ConversationStoreSettings], ConversationStore]
LlmGatewayFactory = Callable[
    [LlmGatewaySettings, ObservabilityProvider, str, LlmCallTraceStore], LlmGateway
]
AgentRunnerFactory = Callable[
    [LlmGateway, ObservabilityProvider, AgentRunnerTraceSink], AgentRunner
]
AgentGraphRuntimeFactory = Callable[[], AgentGraphRuntime]
LogicTraceStoreFactory = Callable[[], LogicTraceStore]
AgentApplicationServiceFactory = Callable[
    [
        RuntimeConfigProvider,
        PetSessionPolicy,
        ConversationStore,
        AgentGraphRuntime,
        AgentLogicTraceStore,
        ObservabilityProvider,
    ],
    AgentApplicationService,
]


@dataclass(slots=True)
class RuntimeComponentBundle:
    """保存组合根创建的应用组件和生命周期所需资源。"""

    app_state: VeterinaryAgentAppState
    checkpoint_provider_factory: CheckpointProviderFactory
    llm_gateway: LlmGateway
    agent_runner: AgentRunner
    logic_trace_store: LogicTraceStore


def create_langgraph_postgres_saver_provider() -> CheckpointProviderLifecycle:
    """创建默认 LangGraph PostgresSaver provider。

    :return: 已按当前环境配置构建但尚未启动的 checkpoint provider。
    :raises ValueError: 当 LangGraph PostgresSaver 配置缺失或非法时抛出。
    """

    return LangGraphPostgresSaverProvider(
        settings=load_langgraph_postgres_saver_settings()
    )


def create_todo_conversation_store(
    settings: ConversationStoreSettings,
) -> ConversationStore:
    """创建默认 ConversationStore TODO 空壳。

    :param settings: ConversationStore 运行配置；当前 TODO 空壳不读取具体字段。
    :return: ConversationStore TODO 空壳。
    """

    del settings
    return TodoConversationStore()


def create_todo_agent_graph_runtime() -> AgentGraphRuntime:
    """创建默认 GraphRuntime TODO 空壳。

    :return: 显式报告未就绪的 GraphRuntime TODO 空壳。
    """

    return TodoAgentGraphRuntime()


def create_todo_logic_trace_store() -> LogicTraceStore:
    """创建默认 LogicTraceStore TODO 空壳。

    :return: 显式返回 Trace 降级状态的 LogicTraceStore TODO 空壳。
    """

    return TodoLogicTraceStore()


def create_runtime_llm_gateway(
    settings: LlmGatewaySettings,
    observability_provider: ObservabilityProvider,
    config_snapshot_id: str,
    trace_store: LlmCallTraceStore,
) -> LlmGateway:
    """创建默认应用内 LlmGateway。

    :param settings: 已校验的 LlmGateway 运行配置。
    :param observability_provider: 已装配的 Observability provider。
    :param config_snapshot_id: 当前 RuntimeConfig 快照 ID。
    :param trace_store: 已适配为 LlmGateway 契约的调用摘要存储。
    :return: 已完成 OpenAI-compatible 适配器装配的 LlmGateway。
    """

    return create_default_llm_gateway(
        settings=settings,
        observability_provider=observability_provider,
        trace_store=trace_store,
        config_snapshot_id=config_snapshot_id,
    )


def create_runtime_agent_runner(
    llm_gateway: LlmGateway,
    observability_provider: ObservabilityProvider,
    trace_sink: AgentRunnerTraceSink,
) -> AgentRunner:
    """创建默认 AgentRunner。

    :param llm_gateway: 已装配的 LlmGateway。
    :param observability_provider: 已装配的 Observability provider。
    :param trace_sink: 已适配为 AgentRunner 契约的运行摘要存储。
    :return: 已装配但可能未就绪的默认 AgentRunner。
    """

    return create_default_agent_runner(
        llm_gateway=llm_gateway,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


def create_default_agent_application_service(
    runtime_config_provider: RuntimeConfigProvider,
    pet_session_policy: PetSessionPolicy,
    conversation_store: ConversationStore,
    graph_runtime: AgentGraphRuntime,
    logic_trace_store: AgentLogicTraceStore,
    observability_provider: ObservabilityProvider,
) -> AgentApplicationService:
    """创建默认 AgentApplicationService。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param pet_session_policy: 已装配的宠物会话策略服务。
    :param conversation_store: 已装配的对话事实存储服务。
    :param graph_runtime: 已装配的 GraphRuntime 应用内端口。
    :param logic_trace_store: 已装配的 LogicTraceStore 应用内端口。
    :param observability_provider: 已装配的 Observability provider。
    :return: 默认 AgentApplicationService 胶水层实现。
    """

    return DefaultAgentApplicationService(
        runtime_config_provider=runtime_config_provider,
        pet_session_policy=pet_session_policy,
        conversation_store=conversation_store,
        graph_runtime=graph_runtime,
        logic_trace_store=logic_trace_store,
        observability_provider=observability_provider,
    )


def build_runtime_component_bundle(
    *,
    settings: ApiIngressSettings | None = None,
    checkpoint_store_settings: CheckpointStoreSettings | None = None,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    runtime_config_settings: RuntimeConfigSettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    checkpoint_provider_factory: CheckpointProviderFactory,
    conversation_store_factory: ConversationStoreFactory,
    llm_gateway_factory: LlmGatewayFactory,
    agent_runner_factory: AgentRunnerFactory,
    graph_runtime_factory: AgentGraphRuntimeFactory,
    logic_trace_store_factory: LogicTraceStoreFactory,
    agent_application_service_factory: AgentApplicationServiceFactory,
) -> RuntimeComponentBundle:
    """解析配置并创建本应用运行所需的全部组件。

    :param settings: 可选 API 接入配置；未传入时从默认配置源加载。
    :param checkpoint_store_settings: 可选 checkpoint 配置；未传入时从默认配置源加载。
    :param conversation_store_settings: 可选对话存储配置；未传入时从默认配置源加载。
    :param llm_gateway_settings: 可选模型网关配置；未传入时从默认配置源加载。
    :param runtime_config_settings: 可选 RuntimeConfig 自身配置；未传入时从默认配置源加载。
    :param observability_settings: 可选可观测性配置；未传入时从默认配置源加载。
    :param checkpoint_provider_factory: checkpoint provider 工厂。
    :param conversation_store_factory: 对话存储工厂。
    :param llm_gateway_factory: 模型网关工厂。
    :param agent_runner_factory: AgentRunner 工厂。
    :param graph_runtime_factory: GraphRuntime 工厂。
    :param logic_trace_store_factory: LogicTraceStore 工厂。
    :param agent_application_service_factory: 应用服务工厂。
    :return: 已完成配置解析和组件装配的运行组件包。
    """

    runtime_config_provider = create_runtime_config_provider(
        runtime_config_settings=runtime_config_settings,
        api_ingress_settings=(
            settings if settings is not None else load_api_ingress_settings()
        ),
        checkpoint_store_settings=(
            checkpoint_store_settings
            if checkpoint_store_settings is not None
            else load_checkpoint_store_settings()
        ),
        conversation_store_settings=(
            conversation_store_settings
            if conversation_store_settings is not None
            else load_conversation_store_settings()
        ),
        llm_gateway_settings=(
            llm_gateway_settings
            if llm_gateway_settings is not None
            else load_llm_gateway_settings()
        ),
        observability_settings=observability_settings,
    )
    snapshot = runtime_config_provider.current_snapshot()
    observability_provider = create_observability_provider(
        settings=snapshot.observability,
    )
    logic_trace_store = logic_trace_store_factory()
    llm_gateway = llm_gateway_factory(
        snapshot.llm_gateway,
        observability_provider,
        snapshot.config_snapshot_id,
        LogicTraceLlmCallTraceStore(logic_trace_store),
    )
    agent_runner = agent_runner_factory(
        llm_gateway,
        observability_provider,
        LogicTraceAgentRunnerTraceSink(logic_trace_store),
    )
    conversation_store = conversation_store_factory(snapshot.conversation_store)
    pet_session_policy = DefaultPetSessionPolicy(
        conversation_store=conversation_store,
        runtime_config_provider=runtime_config_provider,
        observability_provider=observability_provider,
        trace_sink=LogicTracePetSessionTraceSink(logic_trace_store),
    )
    graph_runtime = graph_runtime_factory()
    agent_trace_store = LogicTraceAgentTraceStore(logic_trace_store)
    agent_application_service = agent_application_service_factory(
        runtime_config_provider,
        pet_session_policy,
        conversation_store,
        graph_runtime,
        agent_trace_store,
        observability_provider,
    )
    app_state = VeterinaryAgentAppState(
        settings=snapshot.api_ingress,
        runtime_config_provider=runtime_config_provider,
        runtime_config_snapshot=snapshot,
        started_at=datetime.now(UTC),
        ready=False,
        orchestrator_concurrency_gate=ApiIngressConcurrencyGate(
            max_concurrency=snapshot.api_ingress.orchestrator.max_concurrency,
        ),
        rate_limiter=ApiIngressRateLimiter.from_settings(snapshot.api_ingress),
        checkpoint_store_settings=snapshot.checkpoint_store,
        checkpoint_provider=None,
        checkpoint_provider_ready=False,
        checkpoint_provider_error=None,
        conversation_store_settings=snapshot.conversation_store,
        conversation_store=conversation_store,
        conversation_store_ready=snapshot.conversation_store.enabled,
        conversation_store_error=None,
        pet_session_policy=pet_session_policy,
        pet_session_policy_ready=pet_session_policy.is_ready(),
        llm_gateway_settings=snapshot.llm_gateway,
        llm_gateway=llm_gateway,
        llm_gateway_ready=llm_gateway.is_ready(),
        llm_gateway_error=None,
        agent_runner=agent_runner,
        agent_runner_ready=agent_runner.is_ready(),
        agent_runner_error=None,
        graph_runtime=graph_runtime,
        graph_runtime_ready=graph_runtime.is_ready(),
        logic_trace_store=logic_trace_store,
        logic_trace_store_ready=logic_trace_store.is_ready(),
        agent_application_service=agent_application_service,
        agent_application_service_ready=agent_application_service.is_ready(),
        observability_provider=observability_provider,
        observability_ready=observability_provider.is_ready(),
        observability_error=None,
    )
    return RuntimeComponentBundle(
        app_state=app_state,
        checkpoint_provider_factory=checkpoint_provider_factory,
        llm_gateway=llm_gateway,
        agent_runner=agent_runner,
        logic_trace_store=logic_trace_store,
    )
