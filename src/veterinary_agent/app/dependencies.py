##################################################################################################
# 文件: src/veterinary_agent/app/dependencies.py
# 作用: 提供 FastAPI 请求到应用状态容器的唯一框架依赖入口。
# 边界: 不为每个组件创建透传 getter；组件读取和就绪判定由消费方通过应用状态显式完成。
##################################################################################################

from typing import cast

from fastapi import Request

from veterinary_agent.app.state import VeterinaryAgentAppState
from veterinary_agent.core import APP_STATE_KEY


def get_app_state(request: Request) -> VeterinaryAgentAppState:
    """获取当前 FastAPI 应用的运行状态容器。

    :param request: 当前 HTTP 请求对象。
    :return: 已挂载到 FastAPI ``app.state`` 的运行状态容器。
    :raises RuntimeError: 当应用状态尚未完成初始化时抛出。
    """

    state = getattr(request.app.state, APP_STATE_KEY, None)
    if state is None:
        raise RuntimeError("ASGI 应用状态尚未初始化")
    return cast(VeterinaryAgentAppState, state)


__all__: tuple[str, ...] = ("get_app_state",)
