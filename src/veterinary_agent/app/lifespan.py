##################################################################################################
# 文件: src/veterinary_agent/app/lifespan.py
# 作用: 定义 FastAPI 应用生命周期，负责配置、基础设施、应用服务与 TODO 领域端口的集中装配和关闭期清理。
# 边界: 仅执行组件创建与生命周期管理；不执行数据库迁移、不运行 Agent 请求、不实现 GraphRuntime 或 LogicTraceStore。
##################################################################################################

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from veterinary_agent.agent_application_service import (
    AgentApplicationService,
    AgentGraphRuntime,
    AgentLogicTraceStore,
    DefaultAgentApplicationService,
    TodoAgentGraphRuntime,
    TodoAgentLogicTraceStore,
)
from veterinary_agent.agent_runner import (
    AgentRunner,
    create_default_agent_runner,
)
from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.app.dependencies import APP_STATE_KEY
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)
from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
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
from veterinary_agent.conversation_store import (
    ConversationStore,
    TodoConversationStore,
)
from veterinary_agent.observability import (
    ObservabilityProvider,
    create_observability_provider,
)
from veterinary_agent.llm_gateway import (
    LlmGateway,
    create_default_llm_gateway,
)
from veterinary_agent.pet_session_policy import (
    DefaultPetSessionPolicy,
    PetSessionPolicy,
)

LifespanHandler = Callable[[FastAPI], AbstractAsyncContextManager[None]]
CheckpointProviderFactory = Callable[[], CheckpointProviderLifecycle]
ConversationStoreFactory = Callable[[ConversationStoreSettings], ConversationStore]
LlmGatewayFactory = Callable[
    [LlmGatewaySettings, ObservabilityProvider, str],
    LlmGateway,
]
AgentRunnerFactory = Callable[[LlmGateway, ObservabilityProvider], AgentRunner]
AgentGraphRuntimeFactory = Callable[[], AgentGraphRuntime]
AgentLogicTraceStoreFactory = Callable[[], AgentLogicTraceStore]
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

    :param settings: ConversationStore RuntimeConfig；当前 TODO 空壳不读取具体字段。
    :return: ConversationStore TODO 空壳。
    """

    del settings
    return TodoConversationStore()


def create_todo_agent_graph_runtime() -> AgentGraphRuntime:
    """创建默认 GraphRuntime TODO 空壳。

    :return: 显式报告未就绪的 GraphRuntime TODO 空壳。
    """

    return TodoAgentGraphRuntime()


def create_todo_agent_logic_trace_store() -> AgentLogicTraceStore:
    """创建默认 LogicTraceStore TODO 空壳。

    :return: 显式返回 Trace 降级状态的 LogicTraceStore TODO 空壳。
    """

    return TodoAgentLogicTraceStore()


def create_runtime_llm_gateway(
    settings: LlmGatewaySettings,
    observability_provider: ObservabilityProvider,
    config_snapshot_id: str,
) -> LlmGateway:
    """创建默认应用内 LlmGateway。

    :param settings: 已校验的 LlmGateway RuntimeConfig。
    :param observability_provider: 已装配的 Observability provider。
    :param config_snapshot_id: 当前 RuntimeConfig 快照 ID。
    :return: 已完成 OpenAI-compatible 适配器装配的 LlmGateway。
    """

    return create_default_llm_gateway(
        settings=settings,
        observability_provider=observability_provider,
        config_snapshot_id=config_snapshot_id,
    )


def create_runtime_agent_runner(
    llm_gateway: LlmGateway,
    observability_provider: ObservabilityProvider,
) -> AgentRunner:
    """创建默认 AgentRunner。

    :param llm_gateway: 已装配的 LlmGateway。
    :param observability_provider: 已装配的 Observability provider。
    :return: 已装配但可能未就绪的默认 AgentRunner。
    """

    return create_default_agent_runner(
        llm_gateway=llm_gateway,
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
    :param graph_runtime: 已装配的 GraphRuntime 端口。
    :param logic_trace_store: 已装配的 LogicTraceStore 端口。
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


def _build_checkpoint_provider_start_error(exc: Exception) -> CheckpointStoreError:
    """将 checkpoint provider 启动异常映射为领域错误。

    :param exc: provider 创建或启动阶段捕获的异常。
    :return: 可记录并继续向外抛出的 CheckpointStore 领域错误。
    """

    if isinstance(exc, CheckpointStoreError):
        return exc
    if isinstance(exc, ValueError):
        return CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
            message=str(exc),
            retryable=False,
        )
    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
        message="checkpoint provider 启动失败",
        retryable=True,
    )


def _build_checkpoint_provider_stop_error(exc: Exception) -> CheckpointStoreError:
    """将 checkpoint provider 关闭异常映射为领域错误。

    :param exc: provider 关闭阶段捕获的异常。
    :return: 可记录到 app state 的 CheckpointStore 领域错误。
    """

    if isinstance(exc, CheckpointStoreError):
        return exc
    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_STOP,
        message="checkpoint provider 停止失败",
        retryable=True,
    )


def _build_checkpoint_provider_not_ready_error() -> CheckpointStoreError:
    """构建 checkpoint provider 启动后未就绪错误。

    :return: 表示 provider 启动后仍未就绪的 CheckpointStore 领域错误。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
        message="checkpoint provider 启动后未就绪",
        retryable=True,
    )


async def _cleanup_checkpoint_provider_after_start_failure(
    *,
    app_state: VeterinaryAgentAppState,
    checkpoint_provider: CheckpointProviderLifecycle | None,
) -> None:
    """清理启动失败或启动后未就绪的 checkpoint provider。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :param checkpoint_provider: 启动阶段已经创建的 checkpoint provider。
    :return: None。
    """

    if checkpoint_provider is None:
        app_state.checkpoint_provider = None
        return
    try:
        await checkpoint_provider.stop()
    except Exception as exc:
        checkpoint_error = _build_checkpoint_provider_stop_error(exc)
        app_state.checkpoint_provider_error = checkpoint_error.to_dto()
    finally:
        app_state.checkpoint_provider = None


async def _start_checkpoint_provider(
    *,
    app_state: VeterinaryAgentAppState,
    checkpoint_provider_factory: CheckpointProviderFactory,
) -> None:
    """创建并启动 checkpoint provider。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :param checkpoint_provider_factory: checkpoint provider 工厂。
    :return: None。
    :raises CheckpointStoreError: 当 provider 创建、启动或启动后就绪检查失败时抛出。
    """

    checkpoint_provider: CheckpointProviderLifecycle | None = None
    try:
        checkpoint_provider = checkpoint_provider_factory()
        app_state.checkpoint_provider = checkpoint_provider
        await checkpoint_provider.start()
        if not checkpoint_provider.is_ready():
            raise _build_checkpoint_provider_not_ready_error()
    except Exception as exc:
        checkpoint_error = _build_checkpoint_provider_start_error(exc)
        await _cleanup_checkpoint_provider_after_start_failure(
            app_state=app_state,
            checkpoint_provider=checkpoint_provider,
        )
        app_state.checkpoint_provider_error = checkpoint_error.to_dto()
        app_state.checkpoint_provider_ready = False
        raise checkpoint_error from exc

    app_state.checkpoint_provider_ready = True
    app_state.checkpoint_provider_error = None


async def _stop_checkpoint_provider(app_state: VeterinaryAgentAppState) -> None:
    """停止 app state 中已装配的 checkpoint provider。

    :param app_state: 当前 FastAPI 应用框架级状态。
    :return: None。
    """

    checkpoint_provider = app_state.checkpoint_provider
    app_state.ready = False
    app_state.checkpoint_provider_ready = False
    if checkpoint_provider is None:
        return

    try:
        await checkpoint_provider.stop()
    except Exception as exc:
        checkpoint_error = _build_checkpoint_provider_stop_error(exc)
        app_state.checkpoint_provider_error = checkpoint_error.to_dto()
    finally:
        app_state.checkpoint_provider = None


def create_lifespan(
    settings: ApiIngressSettings | None = None,
    checkpoint_store_settings: CheckpointStoreSettings | None = None,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    runtime_config_settings: RuntimeConfigSettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    checkpoint_provider_factory: CheckpointProviderFactory | None = None,
    conversation_store_factory: ConversationStoreFactory | None = None,
    llm_gateway_factory: LlmGatewayFactory | None = None,
    agent_runner_factory: AgentRunnerFactory | None = None,
    graph_runtime_factory: AgentGraphRuntimeFactory | None = None,
    logic_trace_store_factory: AgentLogicTraceStoreFactory | None = None,
    agent_application_service_factory: AgentApplicationServiceFactory | None = None,
) -> LifespanHandler:
    """创建 FastAPI lifespan 处理器。

    :param settings: 可选的 API 接入组件配置；未传入时从默认配置源加载。
    :param checkpoint_store_settings: 可选 CheckpointStore RuntimeConfig；未传入时从默认配置源加载。
    :param conversation_store_settings: 可选 ConversationStore RuntimeConfig；未传入时从默认配置源加载。
    :param llm_gateway_settings: 可选 LlmGateway RuntimeConfig；未传入时从默认配置源加载。
    :param runtime_config_settings: 可选 RuntimeConfig 组件自身配置；未传入时从默认配置源加载。
    :param observability_settings: 可选 Observability RuntimeConfig；未传入时从默认配置源加载。
    :param checkpoint_provider_factory: 可选 checkpoint provider 工厂；未传入时创建真实 LangGraph PostgresSaver provider。
    :param conversation_store_factory: 可选 ConversationStore 工厂；未传入时创建 TODO 空壳。
    :param llm_gateway_factory: 可选 LlmGateway 工厂；未传入时创建默认 OpenAI-compatible 实现。
    :param agent_runner_factory: 可选 AgentRunner 工厂；未传入时创建默认 AgentRunner。
    :param graph_runtime_factory: 可选 GraphRuntime 工厂；未传入时创建 TODO 空壳。
    :param logic_trace_store_factory: 可选 LogicTraceStore 工厂；未传入时创建 TODO 空壳。
    :param agent_application_service_factory: 可选 AgentApplicationService 工厂；未传入时创建默认胶水层实现。
    :return: 可传入 FastAPI 的 lifespan 处理器。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """管理 FastAPI 应用启动与关闭流程。

        :param app: 当前 FastAPI 应用实例。
        :return: 异步上下文迭代器，无业务返回值。
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
        runtime_config_snapshot = runtime_config_provider.current_snapshot()
        resolved_settings = runtime_config_snapshot.api_ingress
        resolved_checkpoint_store_settings = runtime_config_snapshot.checkpoint_store
        resolved_conversation_store_settings = (
            runtime_config_snapshot.conversation_store
        )
        resolved_observability_settings = runtime_config_snapshot.observability
        resolved_llm_gateway_settings = runtime_config_snapshot.llm_gateway
        observability_provider = create_observability_provider(
            settings=resolved_observability_settings,
        )
        resolved_llm_gateway_factory = (
            llm_gateway_factory
            if llm_gateway_factory is not None
            else create_runtime_llm_gateway
        )
        llm_gateway = resolved_llm_gateway_factory(
            resolved_llm_gateway_settings,
            observability_provider,
            runtime_config_snapshot.config_snapshot_id,
        )
        resolved_agent_runner_factory = (
            agent_runner_factory
            if agent_runner_factory is not None
            else create_runtime_agent_runner
        )
        agent_runner = resolved_agent_runner_factory(
            llm_gateway,
            observability_provider,
        )
        resolved_conversation_store_factory = (
            conversation_store_factory
            if conversation_store_factory is not None
            else create_todo_conversation_store
        )
        conversation_store = resolved_conversation_store_factory(
            resolved_conversation_store_settings
        )
        pet_session_policy = DefaultPetSessionPolicy(
            conversation_store=conversation_store,
            runtime_config_provider=runtime_config_provider,
            observability_provider=observability_provider,
        )
        resolved_graph_runtime_factory = (
            graph_runtime_factory
            if graph_runtime_factory is not None
            else create_todo_agent_graph_runtime
        )
        graph_runtime = resolved_graph_runtime_factory()
        resolved_logic_trace_store_factory = (
            logic_trace_store_factory
            if logic_trace_store_factory is not None
            else create_todo_agent_logic_trace_store
        )
        logic_trace_store = resolved_logic_trace_store_factory()
        resolved_agent_application_service_factory = (
            agent_application_service_factory
            if agent_application_service_factory is not None
            else create_default_agent_application_service
        )
        agent_application_service = resolved_agent_application_service_factory(
            runtime_config_provider,
            pet_session_policy,
            conversation_store,
            graph_runtime,
            logic_trace_store,
            observability_provider,
        )
        resolved_checkpoint_provider_factory = (
            checkpoint_provider_factory
            if checkpoint_provider_factory is not None
            else create_langgraph_postgres_saver_provider
        )
        app_state = VeterinaryAgentAppState(
            settings=resolved_settings,
            runtime_config_provider=runtime_config_provider,
            runtime_config_snapshot=runtime_config_snapshot,
            started_at=datetime.now(UTC),
            ready=False,
            orchestrator_concurrency_gate=ApiIngressConcurrencyGate(
                max_concurrency=resolved_settings.orchestrator.max_concurrency,
            ),
            rate_limiter=ApiIngressRateLimiter.from_settings(resolved_settings),
            checkpoint_store_settings=resolved_checkpoint_store_settings,
            checkpoint_provider=None,
            checkpoint_provider_ready=False,
            checkpoint_provider_error=None,
            conversation_store_settings=resolved_conversation_store_settings,
            conversation_store=conversation_store,
            conversation_store_ready=resolved_conversation_store_settings.enabled,
            conversation_store_error=None,
            pet_session_policy=pet_session_policy,
            pet_session_policy_ready=pet_session_policy.is_ready(),
            llm_gateway_settings=resolved_llm_gateway_settings,
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
        setattr(app.state, APP_STATE_KEY, app_state)
        await _start_checkpoint_provider(
            app_state=app_state,
            checkpoint_provider_factory=resolved_checkpoint_provider_factory,
        )
        app_state.ready = True
        try:
            yield
        finally:
            await llm_gateway.close()
            await agent_runner.close()
            await _stop_checkpoint_provider(app_state)

    return lifespan


__all__: tuple[str, ...] = (
    "AgentApplicationServiceFactory",
    "AgentGraphRuntimeFactory",
    "AgentLogicTraceStoreFactory",
    "CheckpointProviderFactory",
    "ConversationStoreFactory",
    "AgentRunnerFactory",
    "LlmGatewayFactory",
    "LifespanHandler",
    "create_default_agent_application_service",
    "create_runtime_agent_runner",
    "create_langgraph_postgres_saver_provider",
    "create_runtime_llm_gateway",
    "create_todo_agent_graph_runtime",
    "create_todo_agent_logic_trace_store",
    "create_todo_conversation_store",
    "create_lifespan",
)
