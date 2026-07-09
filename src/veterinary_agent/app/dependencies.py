##################################################################################################
# 文件: src/veterinary_agent/app/dependencies.py
# 作用: 定义 FastAPI 框架层依赖获取函数，统一从应用状态读取已装配对象与 checkpoint provider。
# 边界: 仅提供 ASGI App / Framework 层依赖访问，不创建业务组件、不初始化 provider、不执行 Agent 编排逻辑。
##################################################################################################

from typing import NoReturn, cast

from fastapi import Request

from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)
from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    LangGraphCheckpointer,
)
from veterinary_agent.config import ApiIngressSettings, CheckpointStoreSettings

APP_STATE_KEY = "veterinary_agent_state"


def get_app_state(request: Request) -> VeterinaryAgentAppState:
    """获取当前 FastAPI 应用的框架级状态。

    :param request: 当前 HTTP 请求对象。
    :return: 已挂载到 FastAPI app.state 的框架级状态对象。
    :raises RuntimeError: 当应用状态尚未完成初始化时抛出。
    """

    state = getattr(request.app.state, APP_STATE_KEY, None)
    if state is None:
        raise RuntimeError("ASGI 应用状态尚未初始化")
    return cast(VeterinaryAgentAppState, state)


def get_api_ingress_settings(request: Request) -> ApiIngressSettings:
    """获取 API 接入组件配置。

    :param request: 当前 HTTP 请求对象。
    :return: 已加载并通过校验的 API 接入组件配置。
    """

    return get_app_state(request).settings


def get_checkpoint_store_settings(request: Request) -> CheckpointStoreSettings:
    """获取 CheckpointStore RuntimeConfig。

    :param request: 当前 HTTP 请求对象。
    :return: 已加载并通过校验的 CheckpointStore RuntimeConfig。
    :raises RuntimeError: 当 CheckpointStore RuntimeConfig 尚未初始化时抛出。
    """

    settings = get_app_state(request).checkpoint_store_settings
    if settings is None:
        raise RuntimeError("CheckpointStore RuntimeConfig 尚未初始化")
    return settings


def _raise_checkpoint_provider_unavailable(
    *,
    reason: str,
) -> NoReturn:
    """抛出 checkpoint provider 不可用领域错误。

    :param reason: checkpoint provider 不可用原因摘要。
    :return: 该函数总是抛出异常，不会返回。
    :raises CheckpointStoreError: 始终抛出 checkpoint store 不可用错误。
    """

    raise CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_GET,
        message="checkpoint provider 不可用",
        retryable=True,
        conflict_with={"reason": reason},
    )


def get_checkpoint_provider(request: Request) -> CheckpointProviderLifecycle:
    """获取已由 FastAPI lifespan 初始化的 checkpoint provider。

    :param request: 当前 HTTP 请求对象。
    :return: 已启动且可供 GraphRuntime 使用的 checkpoint provider。
    :raises RuntimeError: 当应用状态尚未完成初始化时抛出。
    :raises CheckpointStoreError: 当 checkpoint provider 未装配或未就绪时抛出。
    """

    app_state = get_app_state(request)
    checkpoint_provider = app_state.checkpoint_provider
    if checkpoint_provider is None:
        _raise_checkpoint_provider_unavailable(reason="provider_missing")
    if not app_state.checkpoint_provider_ready:
        _raise_checkpoint_provider_unavailable(reason="provider_state_not_ready")
    if not checkpoint_provider.is_ready():
        _raise_checkpoint_provider_unavailable(reason="provider_not_ready")
    return checkpoint_provider


def get_langgraph_checkpointer(request: Request) -> LangGraphCheckpointer:
    """获取可供 GraphRuntime 编译 LangGraph 图使用的 checkpointer。

    :param request: 当前 HTTP 请求对象。
    :return: 已初始化的 LangGraph checkpointer。
    :raises RuntimeError: 当应用状态尚未完成初始化时抛出。
    :raises CheckpointStoreError: 当 checkpoint provider 未装配、未就绪或 checkpointer 不可用时抛出。
    """

    return get_checkpoint_provider(request).get_checkpointer()


__all__: tuple[str, ...] = (
    "APP_STATE_KEY",
    "get_api_ingress_settings",
    "get_app_state",
    "get_checkpoint_store_settings",
    "get_checkpoint_provider",
    "get_langgraph_checkpointer",
)
