##################################################################################################
# 文件: src/veterinary_agent/app/lifespan.py
# 作用: 定义 FastAPI 应用生命周期，负责配置加载、应用状态初始化、Checkpoint provider 启停与关闭期状态清理。
# 边界: 仅装配 ASGI 框架状态与 L0 checkpoint provider；不执行数据库迁移、不创建 GraphRuntime、不调用兽医业务组件。
##################################################################################################

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

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
    RuntimeConfigSettings,
    create_runtime_config_provider,
    load_api_ingress_settings,
    load_checkpoint_store_settings,
)

LifespanHandler = Callable[[FastAPI], AbstractAsyncContextManager[None]]
CheckpointProviderFactory = Callable[[], CheckpointProviderLifecycle]


def create_langgraph_postgres_saver_provider() -> CheckpointProviderLifecycle:
    """创建默认 LangGraph PostgresSaver provider。

    :return: 已按当前环境配置构建但尚未启动的 checkpoint provider。
    :raises ValueError: 当 LangGraph PostgresSaver 配置缺失或非法时抛出。
    """

    return LangGraphPostgresSaverProvider(
        settings=load_langgraph_postgres_saver_settings()
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
    runtime_config_settings: RuntimeConfigSettings | None = None,
    checkpoint_provider_factory: CheckpointProviderFactory | None = None,
) -> LifespanHandler:
    """创建 FastAPI lifespan 处理器。

    :param settings: 可选的 API 接入组件配置；未传入时从默认配置源加载。
    :param checkpoint_store_settings: 可选 CheckpointStore RuntimeConfig；未传入时从默认配置源加载。
    :param runtime_config_settings: 可选 RuntimeConfig 组件自身配置；未传入时从默认配置源加载。
    :param checkpoint_provider_factory: 可选 checkpoint provider 工厂；未传入时创建真实 LangGraph PostgresSaver provider。
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
        )
        runtime_config_snapshot = runtime_config_provider.current_snapshot()
        resolved_settings = runtime_config_snapshot.api_ingress
        resolved_checkpoint_store_settings = runtime_config_snapshot.checkpoint_store
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
            await _stop_checkpoint_provider(app_state)

    return lifespan


__all__: tuple[str, ...] = (
    "CheckpointProviderFactory",
    "LifespanHandler",
    "create_langgraph_postgres_saver_provider",
    "create_lifespan",
)
