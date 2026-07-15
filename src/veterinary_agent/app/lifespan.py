##################################################################################################
# 文件: src/veterinary_agent/app/lifespan.py
# 作用: 管理 FastAPI 应用启动和关闭阶段，协调组合根创建的资源生命周期。
# 边界: 不创建业务组件、不解析 HTTP 请求、不执行 Agent 编排；组件装配由 app.bootstrap 负责。
##################################################################################################

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

import veterinary_agent.app.bootstrap as runtime_bootstrap
from veterinary_agent.app.bootstrap import (
    AgentApplicationServiceFactory,
    AgentGraphRuntimeFactory,
    AgentRunnerFactory,
    CheckpointProviderFactory,
    ConversationStoreFactory,
    LlmGatewayFactory,
    LogicTraceStoreFactory,
)
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)
from veterinary_agent.core import APP_STATE_KEY
from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
)
from veterinary_agent.config import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    ConversationStoreSettings,
    LlmGatewaySettings,
    ObservabilitySettings,
    RuntimeConfigSettings,
)


LifespanHandler = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _dispose_resource(resource: object) -> None:
    """同步释放带 ``dispose`` 方法的运行期资源。

    :param resource: 需要在 lifespan 退出时尝试释放的资源对象。
    :return: None。
    """

    dispose = getattr(resource, "dispose", None)
    if callable(dispose):
        dispose()


def _build_checkpoint_provider_start_error(exc: Exception) -> CheckpointStoreError:
    """将 checkpoint provider 启动异常映射为稳定领域错误。

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
    """将 checkpoint provider 关闭异常映射为稳定领域错误。

    :param exc: provider 关闭阶段捕获的异常。
    :return: 可记录到应用状态的 CheckpointStore 领域错误。
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
    """构建 provider 启动后仍未就绪的稳定领域错误。

    :return: 表示 provider 未就绪的 CheckpointStore 领域错误。
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
        app_state.checkpoint_provider_error = _build_checkpoint_provider_stop_error(
            exc
        ).to_dto()
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
    :raises CheckpointStoreError: 当 provider 创建、启动或就绪检查失败时抛出。
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
    """停止应用状态中已装配的 checkpoint provider。

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
        app_state.checkpoint_provider_error = _build_checkpoint_provider_stop_error(
            exc
        ).to_dto()
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
    logic_trace_store_factory: LogicTraceStoreFactory | None = None,
    agent_application_service_factory: AgentApplicationServiceFactory | None = None,
) -> LifespanHandler:
    """创建 FastAPI lifespan 处理器。

    :param settings: 可选 API 接入配置；未传入时由组合根加载默认配置。
    :param checkpoint_store_settings: 可选 checkpoint 配置。
    :param conversation_store_settings: 可选对话存储配置。
    :param llm_gateway_settings: 可选模型网关配置。
    :param runtime_config_settings: 可选 RuntimeConfig 自身配置。
    :param observability_settings: 可选可观测性配置。
    :param checkpoint_provider_factory: 可选 checkpoint provider 工厂。
    :param conversation_store_factory: 可选对话存储工厂。
    :param llm_gateway_factory: 可选模型网关工厂。
    :param agent_runner_factory: 可选 AgentRunner 工厂。
    :param graph_runtime_factory: 可选 GraphRuntime 工厂；未传入时默认在 checkpoint provider 启动后装配真实主业务图。
    :param logic_trace_store_factory: 可选 LogicTraceStore 工厂。
    :param agent_application_service_factory: 可选应用服务工厂。
    :return: 可传入 FastAPI 的 lifespan 处理器。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """管理 FastAPI 应用启动、运行和关闭流程。

        :param app: 当前 FastAPI 应用实例。
        :return: 异步上下文迭代器，无业务返回值。
        """

        runtime_database_ready = runtime_bootstrap.has_runtime_database_url()
        use_late_real_graph = graph_runtime_factory is None and runtime_database_ready
        bundle = runtime_bootstrap.build_runtime_component_bundle(
            settings=settings,
            checkpoint_store_settings=checkpoint_store_settings,
            conversation_store_settings=conversation_store_settings,
            llm_gateway_settings=llm_gateway_settings,
            runtime_config_settings=runtime_config_settings,
            observability_settings=observability_settings,
            checkpoint_provider_factory=(
                checkpoint_provider_factory
                if checkpoint_provider_factory is not None
                else (
                    runtime_bootstrap.create_langgraph_postgres_saver_provider
                    if runtime_database_ready
                    else runtime_bootstrap.create_todo_checkpoint_provider
                )
            ),
            conversation_store_factory=(
                conversation_store_factory
                if conversation_store_factory is not None
                else (
                    runtime_bootstrap.create_runtime_conversation_store
                    if runtime_database_ready
                    else runtime_bootstrap.create_todo_conversation_store
                )
            ),
            llm_gateway_factory=(
                llm_gateway_factory
                if llm_gateway_factory is not None
                else runtime_bootstrap.create_runtime_llm_gateway
            ),
            agent_runner_factory=(
                agent_runner_factory
                if agent_runner_factory is not None
                else runtime_bootstrap.create_runtime_agent_runner
            ),
            graph_runtime_factory=(
                graph_runtime_factory
                if graph_runtime_factory is not None
                else (
                    None
                    if use_late_real_graph
                    else runtime_bootstrap.create_todo_agent_graph_runtime
                )
            ),
            logic_trace_store_factory=(
                logic_trace_store_factory
                if logic_trace_store_factory is not None
                else (
                    runtime_bootstrap.create_runtime_logic_trace_store
                    if runtime_database_ready
                    else runtime_bootstrap.create_todo_logic_trace_store
                )
            ),
            agent_application_service_factory=(
                agent_application_service_factory
                if agent_application_service_factory is not None
                else runtime_bootstrap.create_default_agent_application_service
            ),
        )
        app_state = bundle.app_state
        setattr(app.state, APP_STATE_KEY, app_state)
        async with AsyncExitStack() as resources:
            resources.push_async_callback(_stop_checkpoint_provider, app_state)
            resources.push_async_callback(bundle.logic_trace_store.close)
            resources.push_async_callback(bundle.llm_gateway.close)
            resources.push_async_callback(bundle.agent_runner.close)
            await _start_checkpoint_provider(
                app_state=app_state,
                checkpoint_provider_factory=bundle.checkpoint_provider_factory,
            )
            if use_late_real_graph:
                checkpoint_provider = app_state.checkpoint_provider
                if checkpoint_provider is None:
                    raise CheckpointStoreError(
                        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                        operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_GET,
                        message="checkpoint provider 未挂载，无法创建真实 GraphRuntime",
                        retryable=True,
                    )
                graph_bundle = runtime_bootstrap.build_runtime_graph_component_bundle(
                    app_state=app_state,
                    checkpointer=checkpoint_provider.get_checkpointer(),
                    agent_application_service_factory=(
                        agent_application_service_factory
                        if agent_application_service_factory is not None
                        else runtime_bootstrap.create_default_agent_application_service
                    ),
                )
                for resource in graph_bundle.disposable_resources:
                    resources.callback(_dispose_resource, resource)
            app_state.ready = True
            yield

    return lifespan


__all__: tuple[str, ...] = ("create_lifespan",)
