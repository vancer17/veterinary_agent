##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/store.py
# 作用: 定义 LogicTraceStore 的通用写入端口，并提供领域基础设施尚未接入时的 TODO 空壳。
# 边界: 只接受 LogicTraceStore 自有 DTO；不引用或适配任何上层业务领域的数据契约。
##################################################################################################

from typing import Protocol

from veterinary_agent.logic_trace_store.dto import (
    AppendTraceEventCommandDto,
    FinalizeTraceCommandDto,
    LogicTraceWriteResultDto,
    RecordCallSummaryCommandDto,
    RecordTraceArtifactCommandDto,
    StartTraceCommandDto,
)
from veterinary_agent.logic_trace_store.enums import LogicTraceWriteStatus

TODO_TRACE_STORE_ERROR_CODE = "LOGIC_TRACE_STORE_NOT_IMPLEMENTED"
_TODO_TRACE_STORE_DETAIL = "LogicTraceStore 领域依赖尚未接入"


def _build_todo_write_result() -> LogicTraceWriteResultDto:
    """构建统一的 TODO 存储降级结果。

    :return: 标记 LogicTraceStore 尚未接入且允许补偿重试的写入结果。
    """

    return LogicTraceWriteResultDto(
        status=LogicTraceWriteStatus.DEGRADED,
        error_code=TODO_TRACE_STORE_ERROR_CODE,
        retryable=True,
        detail=_TODO_TRACE_STORE_DETAIL,
    )


class LogicTraceStore(Protocol):
    """LogicTraceStore 通用写入端口契约。"""

    def is_ready(self) -> bool:
        """判断 LogicTraceStore 是否具备基础写入能力。

        :return: 若主存储或可靠降级通道可用，则返回 True。
        """

        ...

    async def close(self) -> None:
        """关闭 LogicTraceStore 持有的底层资源。

        :return: None。
        """

        ...

    async def start_trace(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """启动一轮逻辑链。

        :param command: 启动逻辑链的通用命令 DTO。
        :return: 逻辑链启动写入结果。
        """

        ...

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """追加一条逻辑链事件。

        :param command: 追加逻辑链事件的通用命令 DTO。
        :return: 逻辑链事件写入结果。
        """

        ...

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一次调用摘要。

        :param command: 记录调用摘要的通用命令 DTO。
        :return: 调用摘要写入结果。
        """

        ...

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一个 trace artifact。

        :param command: 记录 trace artifact 的通用命令 DTO。
        :return: artifact 写入结果。
        """

        ...

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """完成一轮逻辑链。

        :param command: 完成逻辑链的通用命令 DTO。
        :return: 逻辑链完成写入结果。
        """

        ...


class TodoLogicTraceStore:
    """LogicTraceStore 尚未接入时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO LogicTraceStore 是否就绪。

        :return: 固定返回 False，表示真实存储尚未接入。
        """

        return False

    async def close(self) -> None:
        """关闭 TODO LogicTraceStore。

        :return: None；TODO 空壳不持有底层资源。
        """

        return None

    async def start_trace(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 Trace 启动降级结果。

        :param command: 启动逻辑链的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return _build_todo_write_result()

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 Trace 事件追加降级结果。

        :param command: 追加逻辑链事件的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return _build_todo_write_result()

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回调用摘要写入降级结果。

        :param command: 记录调用摘要的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return _build_todo_write_result()

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 artifact 写入降级结果。

        :param command: 记录 trace artifact 的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return _build_todo_write_result()

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 Trace 完成降级结果。

        :param command: 完成逻辑链的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return _build_todo_write_result()


__all__: tuple[str, ...] = (
    "LogicTraceStore",
    "TODO_TRACE_STORE_ERROR_CODE",
    "TodoLogicTraceStore",
)
