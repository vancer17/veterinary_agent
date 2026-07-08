##################################################################################################
# 文件: src/veterinary_agent/app/dependencies.py
# 作用: 定义 FastAPI 框架层依赖获取函数，统一从应用状态读取已装配对象。
# 边界: 仅提供 ASGI App / Framework 层依赖访问，不创建业务组件或执行 Agent 编排逻辑。
##################################################################################################

from typing import cast

from fastapi import Request

from veterinary_agent.app.state import VeterinaryAgentAppState
from veterinary_agent.config import ApiIngressSettings

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


__all__: tuple[str, ...] = (
    "APP_STATE_KEY",
    "get_api_ingress_settings",
    "get_app_state",
)
