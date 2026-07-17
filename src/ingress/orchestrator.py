"""
文件：src/ingress/orchestrator.py
作用：提供外部 API 入口、请求 DTO、错误处理与编排器适配。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from .dto import AgentTurnRequest
from .errors import OrchestratorUnavailableError


class Orchestrator(Protocol):
    async def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        ...

    async def create_turn(self, request: AgentTurnRequest) -> Mapping[str, Any]:
        """创建一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        ...

    def stream_turn(
        self, request: AgentTurnRequest
    ) -> AsyncIterator[Mapping[str, Any]]:
        """以流式事件形式执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        ...


class UnavailableOrchestrator:
    async def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return False

    async def create_turn(self, request: AgentTurnRequest) -> Mapping[str, Any]:
        """创建一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        raise OrchestratorUnavailableError(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
        )

    async def stream_turn(
        self, request: AgentTurnRequest
    ) -> AsyncIterator[Mapping[str, Any]]:
        """以流式事件形式执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        raise OrchestratorUnavailableError(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
        )
        yield {}


_orchestrator: Orchestrator | None = None


def set_orchestrator(orchestrator: Orchestrator) -> None:
    """设置全局入口编排器。

    :param orchestrator: 编排器实例。
    :return: 返回函数执行结果。
    """
    global _orchestrator
    _orchestrator = orchestrator


async def get_orchestrator() -> Orchestrator:
    """获取全局入口编排器。

    :return: 返回函数执行结果。
    """
    global _orchestrator
    if _orchestrator is None:
        from vet_agent import get_container
        from vet_agent import VetAgentIngressOrchestrator

        _orchestrator = VetAgentIngressOrchestrator(get_container())
    return _orchestrator
