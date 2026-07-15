##################################################################################################
# 文件: src/veterinary_agent/app/bootstrap.py
# 作用: 定义应用组合根，集中解析运行配置、创建基础设施组件、真实主业务图运行时和 FastAPI 应用状态。
# 边界: 只负责依赖装配与 TODO 领域占位选择；不管理 ASGI 生命周期、不处理 HTTP 请求、不执行领域业务。
##################################################################################################

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol, cast, runtime_checkable

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
    AgentSpecRegistry,
    LogicTraceAgentRunnerTraceSink,
    create_default_agent_runner,
)
from veterinary_agent.agent_spec_registry import create_default_agent_spec_registry
from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)
from veterinary_agent.checkpoint_store import (
    CheckpointStore,
    LangGraphCheckpointReader,
    LangGraphCheckpointWriter,
    LangGraphCheckpointer,
    LangGraphPostgresSaverProvider,
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
    create_sqlalchemy_checkpoint_store,
    load_checkpoint_store_migration_settings,
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
from veterinary_agent.conversation_store import (
    ConversationStore,
    TodoConversationStore,
    create_sqlalchemy_conversation_store,
)
from veterinary_agent.education_agent import (
    LogicTraceEducationTraceSink,
    create_default_education_agent,
)
from veterinary_agent.graph_runtime import (
    DefaultGraphRuntime,
    GraphRuntimeSettings,
    create_default_graph_runtime,
)
from veterinary_agent.guardrail_framework import (
    GuardrailFramework,
    LogicTraceGuardrailTraceSink,
    create_default_guardrail_framework,
)
from veterinary_agent.llm_gateway import (
    LlmCallTraceStore,
    LlmGateway,
    LogicTraceLlmCallTraceStore,
    create_default_llm_gateway,
)
from veterinary_agent.logic_trace_store import (
    LogicTraceStore,
    TodoLogicTraceStore,
    create_sqlalchemy_logic_trace_store,
)
from veterinary_agent.nonmedical_pet_care_agent import (
    LogicTraceNonmedicalPetCareTraceSink,
    create_default_nonmedical_pet_care_agent,
)
from veterinary_agent.observability import (
    ObservabilityProvider,
    create_observability_provider,
)
from veterinary_agent.pet_session_policy import (
    DefaultPetSessionPolicy,
    LogicTracePetSessionTraceSink,
    PetSessionPolicy,
)
from veterinary_agent.safety_trigger_agent import (
    LogicTraceSafetyTriggerTraceSink,
    create_default_safety_trigger_agent,
)
from veterinary_agent.standard_consultation_agent import (
    LogicTraceStandardConsultationTraceSink,
    create_default_standard_consultation_agent,
)
from veterinary_agent.vet_context_builder import (
    LogicTraceVetContextTraceSink,
    build_default_context_source_ports,
    create_default_vet_context_builder,
)
from veterinary_agent.vet_conversation_graph import (
    build_vet_conversation_graph_registry,
)
from veterinary_agent.vet_input_safety_assessor import (
    LogicTraceVetInputSafetyTraceSink,
    create_default_vet_input_safety_assessor,
)
from veterinary_agent.vet_response_composer import (
    LogicTraceVetResponseComposerTraceSink,
    create_default_vet_response_composer,
)
from veterinary_agent.vet_task_decomposer import (
    LogicTraceVetTaskDecomposerTraceSink,
    create_default_vet_task_decomposer,
)


CheckpointProviderFactory = Callable[[], CheckpointProviderLifecycle]
ConversationStoreFactory = Callable[[ConversationStoreSettings], ConversationStore]
LlmGatewayFactory = Callable[
    [LlmGatewaySettings, ObservabilityProvider, str, LlmCallTraceStore], LlmGateway
]
AgentRunnerFactory = Callable[
    [LlmGateway, ObservabilityProvider, AgentRunnerTraceSink, AgentSpecRegistry],
    AgentRunner,
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


@runtime_checkable
class DisposableResource(Protocol):
    """应用关闭阶段可同步释放底层资源的协议。"""

    def dispose(self) -> None:
        """释放当前资源持有的底层句柄。

        :return: None。
        """

        ...


@dataclass(slots=True)
class RuntimeComponentBundle:
    """保存组合根创建的应用组件和生命周期所需资源。"""

    app_state: VeterinaryAgentAppState
    checkpoint_provider_factory: CheckpointProviderFactory
    llm_gateway: LlmGateway
    agent_runner: AgentRunner
    guardrail_framework: GuardrailFramework
    logic_trace_store: LogicTraceStore


@dataclass(slots=True)
class RuntimeGraphComponentBundle:
    """保存 checkpoint provider 启动后创建的真实图运行组件。"""

    checkpoint_store: CheckpointStore
    graph_runtime: AgentGraphRuntime
    agent_application_service: AgentApplicationService
    disposable_resources: tuple[DisposableResource, ...] = ()


def create_langgraph_postgres_saver_provider() -> CheckpointProviderLifecycle:
    """创建默认 LangGraph PostgresSaver provider。

    :return: 已按当前环境配置构建但尚未启动的 checkpoint provider。
    :raises ValueError: 当 LangGraph PostgresSaver 配置缺失或非法时抛出。
    """

    return LangGraphPostgresSaverProvider(
        settings=load_langgraph_postgres_saver_settings()
    )


def _load_database_url() -> str:
    """读取应用真实存储共用的数据库连接地址。

    :return: 从统一迁移配置中读取的数据库连接地址。
    :raises ValueError: 当 DATABASE_URL 未配置或为空时抛出。
    """

    return load_checkpoint_store_migration_settings().database_url


def has_runtime_database_url() -> bool:
    """判断当前进程是否已配置真实运行链路数据库地址。

    :return: 若 DATABASE_URL 可读取且非空，则返回 True。
    """

    try:
        _load_database_url()
    except ValueError:
        return False
    return True


class TodoCheckpointProvider:
    """缺少真实数据库时用于测试和 fail-closed 降级的 checkpoint provider。"""

    def __init__(self) -> None:
        """初始化 TODO checkpoint provider。

        :return: None。
        """

        self._ready = False

    async def start(self) -> None:
        """启动 TODO checkpoint provider。

        :return: None。
        """

        self._ready = True

    async def stop(self) -> None:
        """停止 TODO checkpoint provider。

        :return: None。
        """

        self._ready = False

    def is_ready(self) -> bool:
        """判断 TODO checkpoint provider 是否已启动。

        :return: 若 provider 已启动，则返回 True。
        """

        return self._ready

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取 TODO checkpointer 占位对象。

        :return: 仅用于不触发真实图编译路径的 checkpointer 占位对象。
        :raises RuntimeError: 当 provider 尚未启动时抛出。
        """

        if not self._ready:
            raise RuntimeError("TODO checkpoint provider 尚未启动")
        return cast(LangGraphCheckpointer, object())

    def build_config(
        self,
        *,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> LangGraphRunnableConfig:
        """构建 LangGraph thread 运行配置。

        :param thread_id: LangGraph checkpointer 使用的 thread ID。
        :param checkpoint_id: 可选 checkpoint ID。
        :return: 可传递给 LangGraph 的运行配置。
        """

        return build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )


def create_todo_checkpoint_provider() -> CheckpointProviderLifecycle:
    """创建默认 checkpoint provider TODO 空壳。

    :return: 不连接数据库且只服务 TODO 图降级路径的 checkpoint provider。
    """

    return TodoCheckpointProvider()


def create_todo_conversation_store(
    settings: ConversationStoreSettings,
) -> ConversationStore:
    """创建默认 ConversationStore TODO 空壳。

    :param settings: ConversationStore 运行配置；当前 TODO 空壳不读取具体字段。
    :return: ConversationStore TODO 空壳。
    """

    del settings
    return TodoConversationStore()


def create_runtime_conversation_store(
    settings: ConversationStoreSettings,
) -> ConversationStore:
    """创建默认真实 ConversationStore。

    :param settings: ConversationStore 运行配置。
    :return: 基于 SQLAlchemy 的 ConversationStore 实例。
    :raises ValueError: 当 DATABASE_URL 缺失或非法时抛出。
    """

    return create_sqlalchemy_conversation_store(
        _load_database_url(),
        settings=settings,
    )


def create_todo_agent_graph_runtime() -> AgentGraphRuntime:
    """创建默认 GraphRuntime TODO 空壳。

    :return: 显式报告未就绪的 GraphRuntime TODO 空壳。
    """

    return TodoAgentGraphRuntime()


def create_runtime_checkpoint_store(
    *,
    settings: CheckpointStoreSettings,
    checkpointer: LangGraphCheckpointer,
) -> CheckpointStore:
    """创建默认真实 CheckpointStore。

    :param settings: CheckpointStore 运行配置。
    :param checkpointer: 已由 lifespan 启动的 LangGraph checkpointer。
    :return: 基于 SQLAlchemy 控制面与同源 LangGraph checkpointer 的 CheckpointStore。
    :raises ValueError: 当 DATABASE_URL 缺失或非法时抛出。
    """

    checkpoint_reader = LangGraphCheckpointReader(checkpointer)
    checkpoint_writer = LangGraphCheckpointWriter(checkpointer)
    return create_sqlalchemy_checkpoint_store(
        _load_database_url(),
        settings=settings,
        checkpoint_reader=checkpoint_reader,
        checkpoint_writer=checkpoint_writer,
    )


def create_runtime_logic_trace_store() -> LogicTraceStore:
    """创建默认真实 LogicTraceStore。

    :return: 基于 SQLAlchemy 的 LogicTraceStore 实例。
    :raises ValueError: 当 DATABASE_URL 缺失或非法时抛出。
    """

    return create_sqlalchemy_logic_trace_store(_load_database_url())


def create_todo_logic_trace_store() -> LogicTraceStore:
    """创建默认 LogicTraceStore TODO 空壳。

    :return: 显式返回 Trace 降级状态的 LogicTraceStore TODO 空壳。
    """

    return TodoLogicTraceStore()


def _require_runtime_config_provider(
    app_state: VeterinaryAgentAppState,
) -> RuntimeConfigProvider:
    """从应用状态读取必需的 RuntimeConfig provider。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 RuntimeConfig provider。
    :raises RuntimeError: 当 RuntimeConfig provider 尚未装配时抛出。
    """

    provider = app_state.runtime_config_provider
    if provider is None:
        raise RuntimeError("RuntimeConfig provider 尚未初始化")
    return provider


def _require_conversation_store(
    app_state: VeterinaryAgentAppState,
) -> ConversationStore:
    """从应用状态读取必需的 ConversationStore。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 ConversationStore。
    :raises RuntimeError: 当 ConversationStore 尚未装配时抛出。
    """

    store = app_state.conversation_store
    if store is None:
        raise RuntimeError("ConversationStore 尚未初始化")
    return store


def _require_agent_runner(app_state: VeterinaryAgentAppState) -> AgentRunner:
    """从应用状态读取必需的 AgentRunner。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 AgentRunner。
    :raises RuntimeError: 当 AgentRunner 尚未装配时抛出。
    """

    runner = app_state.agent_runner
    if runner is None:
        raise RuntimeError("AgentRunner 尚未初始化")
    return runner


def _require_guardrail_framework(
    app_state: VeterinaryAgentAppState,
) -> GuardrailFramework:
    """从应用状态读取必需的 GuardrailFramework。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 GuardrailFramework。
    :raises RuntimeError: 当 GuardrailFramework 尚未装配时抛出。
    """

    framework = app_state.guardrail_framework
    if framework is None:
        raise RuntimeError("GuardrailFramework 尚未初始化")
    return framework


def _require_logic_trace_store(
    app_state: VeterinaryAgentAppState,
) -> LogicTraceStore:
    """从应用状态读取必需的 LogicTraceStore。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 LogicTraceStore。
    :raises RuntimeError: 当 LogicTraceStore 尚未装配时抛出。
    """

    store = app_state.logic_trace_store
    if store is None:
        raise RuntimeError("LogicTraceStore 尚未初始化")
    return store


def _require_observability_provider(
    app_state: VeterinaryAgentAppState,
) -> ObservabilityProvider:
    """从应用状态读取必需的 Observability provider。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 Observability provider。
    :raises RuntimeError: 当 Observability provider 尚未装配时抛出。
    """

    provider = app_state.observability_provider
    if provider is None:
        raise RuntimeError("Observability provider 尚未初始化")
    return provider


def _require_pet_session_policy(
    app_state: VeterinaryAgentAppState,
) -> PetSessionPolicy:
    """从应用状态读取必需的 PetSessionPolicy。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: 已装配的 PetSessionPolicy。
    :raises RuntimeError: 当 PetSessionPolicy 尚未装配时抛出。
    """

    policy = app_state.pet_session_policy
    if policy is None:
        raise RuntimeError("PetSessionPolicy 尚未初始化")
    return policy


def _build_runtime_graph(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    conversation_store: ConversationStore,
    checkpoint_store: CheckpointStore,
    checkpointer: LangGraphCheckpointer,
    agent_runner: AgentRunner,
    guardrail_framework: GuardrailFramework,
    logic_trace_store: LogicTraceStore,
    observability_provider: ObservabilityProvider,
) -> DefaultGraphRuntime:
    """创建已注册真实兽医主业务图的默认 GraphRuntime。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param conversation_store: ConversationStore 用户可见消息事实存储。
    :param checkpoint_store: CheckpointStore 项目控制面存储。
    :param checkpointer: 已由 lifespan 启动的 LangGraph checkpointer。
    :param agent_runner: AgentRunner 受控模型调用入口。
    :param guardrail_framework: 护栏框架服务。
    :param logic_trace_store: LogicTraceStore 领域 trace 存储。
    :param observability_provider: Observability provider。
    :return: 已编译并注册真实主业务图的 GraphRuntime。
    """

    task_decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceVetTaskDecomposerTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    input_safety_assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceVetInputSafetyTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    context_builder = create_default_vet_context_builder(
        runtime_config_provider=runtime_config_provider,
        source_ports=build_default_context_source_ports(
            conversation_store=conversation_store,
            checkpoint_store=checkpoint_store,
        ),
        trace_sink=LogicTraceVetContextTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    standard_consultation_agent = create_default_standard_consultation_agent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceStandardConsultationTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    education_agent = create_default_education_agent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceEducationTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    safety_trigger_agent = create_default_safety_trigger_agent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceSafetyTriggerTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    nonmedical_pet_care_agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        trace_sink=LogicTraceNonmedicalPetCareTraceSink(store=logic_trace_store),
        observability_provider=observability_provider,
    )
    response_composer = create_default_vet_response_composer(
        runtime_config_provider=runtime_config_provider,
        conversation_store=conversation_store,
        checkpoint_store=checkpoint_store,
        trace_sink=LogicTraceVetResponseComposerTraceSink(store=logic_trace_store),
    )
    graph_registry = build_vet_conversation_graph_registry(
        task_decomposer=task_decomposer,
        input_safety_assessor=input_safety_assessor,
        context_builder=context_builder,
        standard_consultation_agent=standard_consultation_agent,
        education_agent=education_agent,
        safety_trigger_agent=safety_trigger_agent,
        nonmedical_pet_care_agent=nonmedical_pet_care_agent,
        guardrail_framework=guardrail_framework,
        response_composer=response_composer,
    )
    return create_default_graph_runtime(
        checkpoint_store=checkpoint_store,
        checkpointer=checkpointer,
        graph_registry=graph_registry,
        settings=GraphRuntimeSettings(),
        observability_provider=observability_provider,
    )


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
    spec_registry: AgentSpecRegistry,
) -> AgentRunner:
    """创建默认 AgentRunner。

    :param llm_gateway: 已装配的 LlmGateway。
    :param observability_provider: 已装配的 Observability provider。
    :param trace_sink: 已适配为 AgentRunner 契约的运行摘要存储。
    :param spec_registry: 已根据 RuntimeConfig 快照构建的 Agent 规格注册表。
    :return: 已装配但可能未就绪的默认 AgentRunner。
    """

    return create_default_agent_runner(
        llm_gateway=llm_gateway,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
        spec_registry=spec_registry,
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
    graph_runtime_factory: AgentGraphRuntimeFactory | None,
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
    :param agent_runner_factory: AgentRunner 工厂，接收已构建的 Agent 规格注册表。
    :param graph_runtime_factory: 可选 GraphRuntime 工厂；为空时由 lifespan 在 checkpoint provider 启动后创建真实图运行时。
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
    agent_spec_registry = create_default_agent_spec_registry(snapshot)
    agent_runner = agent_runner_factory(
        llm_gateway,
        observability_provider,
        LogicTraceAgentRunnerTraceSink(logic_trace_store),
        agent_spec_registry,
    )
    guardrail_framework = create_default_guardrail_framework(
        runtime_config_provider=runtime_config_provider,
        observability_provider=observability_provider,
        trace_sink=LogicTraceGuardrailTraceSink(store=logic_trace_store),
    )
    conversation_store = conversation_store_factory(snapshot.conversation_store)
    pet_session_policy = DefaultPetSessionPolicy(
        conversation_store=conversation_store,
        runtime_config_provider=runtime_config_provider,
        observability_provider=observability_provider,
        trace_sink=LogicTracePetSessionTraceSink(logic_trace_store),
    )
    graph_runtime = (
        graph_runtime_factory() if graph_runtime_factory is not None else None
    )
    agent_trace_store = LogicTraceAgentTraceStore(logic_trace_store)
    agent_application_service = (
        agent_application_service_factory(
            runtime_config_provider,
            pet_session_policy,
            conversation_store,
            graph_runtime,
            agent_trace_store,
            observability_provider,
        )
        if graph_runtime is not None
        else None
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
        checkpoint_store=None,
        checkpoint_store_ready=(
            graph_runtime.is_ready() if graph_runtime is not None else False
        ),
        checkpoint_store_error=None,
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
        guardrail_framework=guardrail_framework,
        guardrail_framework_ready=guardrail_framework.is_ready(),
        guardrail_framework_error=None,
        graph_runtime=graph_runtime,
        graph_runtime_ready=(
            graph_runtime.is_ready() if graph_runtime is not None else False
        ),
        logic_trace_store=logic_trace_store,
        logic_trace_store_ready=logic_trace_store.is_ready(),
        agent_application_service=agent_application_service,
        agent_application_service_ready=(
            agent_application_service.is_ready()
            if agent_application_service is not None
            else False
        ),
        observability_provider=observability_provider,
        observability_ready=observability_provider.is_ready(),
        observability_error=None,
    )
    return RuntimeComponentBundle(
        app_state=app_state,
        checkpoint_provider_factory=checkpoint_provider_factory,
        llm_gateway=llm_gateway,
        agent_runner=agent_runner,
        guardrail_framework=guardrail_framework,
        logic_trace_store=logic_trace_store,
    )


def build_runtime_graph_component_bundle(
    *,
    app_state: VeterinaryAgentAppState,
    checkpointer: LangGraphCheckpointer,
    agent_application_service_factory: AgentApplicationServiceFactory,
) -> RuntimeGraphComponentBundle:
    """在 checkpoint provider 启动后创建真实主业务图运行组件。

    :param app_state: 已完成基础组件装配的 FastAPI 应用框架级状态。
    :param checkpointer: checkpoint provider 暴露的 LangGraph checkpointer。
    :param agent_application_service_factory: 应用服务工厂。
    :return: 已创建并写回 app_state 的真实图运行组件包。
    :raises RuntimeError: 当基础依赖尚未装配时抛出。
    :raises ValueError: 当真实存储所需 DATABASE_URL 缺失或非法时抛出。
    """

    runtime_config_provider = _require_runtime_config_provider(app_state)
    conversation_store = _require_conversation_store(app_state)
    agent_runner = _require_agent_runner(app_state)
    guardrail_framework = _require_guardrail_framework(app_state)
    logic_trace_store = _require_logic_trace_store(app_state)
    observability_provider = _require_observability_provider(app_state)
    pet_session_policy = _require_pet_session_policy(app_state)
    checkpoint_settings = app_state.checkpoint_store_settings
    if checkpoint_settings is None:
        raise RuntimeError("CheckpointStore RuntimeConfig 尚未初始化")

    checkpoint_store = create_runtime_checkpoint_store(
        settings=checkpoint_settings,
        checkpointer=checkpointer,
    )
    graph_runtime = _build_runtime_graph(
        runtime_config_provider=runtime_config_provider,
        conversation_store=conversation_store,
        checkpoint_store=checkpoint_store,
        checkpointer=checkpointer,
        agent_runner=agent_runner,
        guardrail_framework=guardrail_framework,
        logic_trace_store=logic_trace_store,
        observability_provider=observability_provider,
    )
    agent_application_service = agent_application_service_factory(
        runtime_config_provider,
        pet_session_policy,
        conversation_store,
        graph_runtime,
        LogicTraceAgentTraceStore(logic_trace_store),
        observability_provider,
    )

    app_state.checkpoint_store = checkpoint_store
    app_state.checkpoint_store_ready = True
    app_state.checkpoint_store_error = None
    app_state.graph_runtime = graph_runtime
    app_state.graph_runtime_ready = graph_runtime.is_ready()
    app_state.agent_application_service = agent_application_service
    app_state.agent_application_service_ready = agent_application_service.is_ready()
    return RuntimeGraphComponentBundle(
        checkpoint_store=checkpoint_store,
        graph_runtime=graph_runtime,
        agent_application_service=agent_application_service,
        disposable_resources=(
            (checkpoint_store,)
            if isinstance(checkpoint_store, DisposableResource)
            else ()
        ),
    )
