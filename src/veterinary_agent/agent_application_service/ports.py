##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/ports.py
# 作用: 定义 AgentApplicationService 对 GraphRuntime 与 LogicTraceStore 的应用内端口，并提供领域依赖未实现时的 TODO 空壳。
# 边界: 不实现真实图编排、checkpoint、Trace 持久化或业务节点；TODO 空壳仅返回显式降级或不可用结果。
##################################################################################################

from collections.abc import AsyncIterator
from typing import Protocol, Self

from veterinary_agent.agent_application_service.dto import (
    AgentCancelTurnCommandDto,
    AgentCancelTurnResultDto,
    AgentGraphEventDto,
    AgentGraphTurnRequestDto,
    AgentGraphTurnResultDto,
    AgentResumeTurnCommandDto,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
)
from veterinary_agent.agent_application_service.enums import (
    AgentTraceDeliveryStatus,
)

TODO_GRAPH_RUNTIME_ERROR_CODE = "AGENT_GRAPH_RUNTIME_NOT_IMPLEMENTED"
TODO_TRACE_STORE_ERROR_CODE = "AGENT_LOGIC_TRACE_STORE_NOT_IMPLEMENTED"


class _UnavailableGraphEventStream:
    """始终在拉取事件时报告 GraphRuntime 不可用的异步迭代器。"""

    def __aiter__(self) -> Self:
        """返回异步迭代器自身。

        :return: 当前异步迭代器实例。
        """

        return self

    async def __anext__(self) -> AgentGraphEventDto:
        """拒绝读取下一条 GraphRuntime 事件。

        :return: 当前实现不会返回事件。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        raise AgentGraphRuntimeUnavailableError()


class AgentGraphRuntimeUnavailableError(RuntimeError):
    """GraphRuntime 端口不可用异常。"""

    def __init__(
        self,
        message: str = "GraphRuntime 领域依赖尚未接入",
    ) -> None:
        """初始化 GraphRuntime 不可用异常。

        :param message: 面向工程排障的错误说明。
        :return: None。
        """

        self.code = TODO_GRAPH_RUNTIME_ERROR_CODE
        super().__init__(message)


class AgentGraphRuntime(Protocol):
    """AgentApplicationService 使用的 GraphRuntime 端口。"""

    def is_ready(self) -> bool:
        """判断 GraphRuntime 是否具备执行条件。

        :return: 若 GraphRuntime 已注册业务图且依赖就绪，则返回 True。
        """

        ...

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """同步执行一轮业务图。

        :param request: 已绑定应用执行上下文的图运行请求。
        :return: GraphRuntime 最终结果。
        :raises AgentGraphRuntimeUnavailableError: 当 GraphRuntime 尚未接入时抛出。
        """

        ...

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """流式执行一轮业务图。

        :param request: 已绑定应用执行上下文的图运行请求。
        :return: GraphRuntime 协议无关事件异步迭代器。
        :raises AgentGraphRuntimeUnavailableError: 当 GraphRuntime 尚未接入时抛出。
        """

        ...

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """恢复一轮未完成业务图。

        :param command: 恢复运行命令。
        :return: GraphRuntime 恢复事件异步迭代器。
        :raises AgentGraphRuntimeUnavailableError: 当 GraphRuntime 尚未接入时抛出。
        """

        ...

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """取消正在执行的业务图。

        :param command: 取消运行命令。
        :return: GraphRuntime 取消结果。
        :raises AgentGraphRuntimeUnavailableError: 当 GraphRuntime 尚未接入时抛出。
        """

        ...


class AgentLogicTraceStore(Protocol):
    """AgentApplicationService 使用的 LogicTraceStore 端口。"""

    def is_ready(self) -> bool:
        """判断 LogicTraceStore 是否具备基础写入能力。

        :return: 若 Trace 主存储或可靠降级通道可用，则返回 True。
        """

        ...

    async def start_trace(
        self,
        command: AgentTraceStartCommandDto,
    ) -> AgentTraceWriteResultDto:
        """启动一轮 Agent 逻辑链。

        :param command: 逻辑链启动命令。
        :return: Trace 启动写入结果。
        """

        ...

    async def finalize_trace(
        self,
        command: AgentTraceFinalizeCommandDto,
    ) -> AgentTraceWriteResultDto:
        """完成一轮 Agent 逻辑链。

        :param command: 逻辑链完成命令。
        :return: Trace 完成写入结果。
        """

        ...


class TodoAgentGraphRuntime:
    """GraphRuntime 尚未实现时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO GraphRuntime 是否就绪。

        :return: 固定返回 False。
        """

        return False

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """拒绝同步执行请求。

        :param request: 图运行请求；TODO 空壳不消费其业务内容。
        :return: 当前实现不会返回结果。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        del request
        raise AgentGraphRuntimeUnavailableError()

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """拒绝流式执行请求。

        :param request: 图运行请求；TODO 空壳不消费其业务内容。
        :return: 当前实现不会产生事件。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        del request
        return _UnavailableGraphEventStream()

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """拒绝恢复执行请求。

        :param command: 恢复运行命令；TODO 空壳不消费其内容。
        :return: 当前实现不会产生事件。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        del command
        return _UnavailableGraphEventStream()

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """拒绝取消执行请求。

        :param command: 取消运行命令；TODO 空壳不消费其内容。
        :return: 当前实现不会返回结果。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        del command
        raise AgentGraphRuntimeUnavailableError()


class TodoAgentLogicTraceStore:
    """LogicTraceStore 尚未实现时使用的显式降级 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO LogicTraceStore 是否就绪。

        :return: 固定返回 False，表示当前仅能显式降级。
        """

        return False

    async def start_trace(
        self,
        command: AgentTraceStartCommandDto,
    ) -> AgentTraceWriteResultDto:
        """返回 Trace 启动降级结果。

        :param command: 逻辑链启动命令；TODO 空壳不持久化该命令。
        :return: 表示 LogicTraceStore 尚未实现的降级结果。
        """

        del command
        return AgentTraceWriteResultDto(
            status=AgentTraceDeliveryStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def finalize_trace(
        self,
        command: AgentTraceFinalizeCommandDto,
    ) -> AgentTraceWriteResultDto:
        """返回 Trace 完成降级结果。

        :param command: 逻辑链完成命令；TODO 空壳不持久化该命令。
        :return: 表示 LogicTraceStore 尚未实现的降级结果。
        """

        del command
        return AgentTraceWriteResultDto(
            status=AgentTraceDeliveryStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )


__all__: tuple[str, ...] = (
    "TODO_GRAPH_RUNTIME_ERROR_CODE",
    "TODO_TRACE_STORE_ERROR_CODE",
    "AgentGraphRuntime",
    "AgentGraphRuntimeUnavailableError",
    "AgentLogicTraceStore",
    "TodoAgentGraphRuntime",
    "TodoAgentLogicTraceStore",
)
