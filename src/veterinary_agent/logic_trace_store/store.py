##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/store.py
# 作用: 定义 LogicTraceStore 的应用内端口契约，并提供领域依赖尚未接入时的 TODO 空壳实现。
# 边界: 仅声明逻辑链留痕组件的稳定入口，不实现数据库、VetTraceSchema、事件总线或业务编排。
##################################################################################################

from typing import Protocol

from veterinary_agent.agent_application_service import (
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
)
from veterinary_agent.agent_runner import (
    AgentRunSummaryDto,
    AgentRunnerTraceWriteResultDto,
    AgentRunnerTraceWriteStatus,
)
from veterinary_agent.llm_gateway import (
    LlmCallSummaryDto,
    LlmTraceWriteResultDto,
    LlmTraceWriteStatus,
)
from veterinary_agent.logic_trace_store.dto import (
    AppendTraceEventCommandDto,
    FinalizeTraceCommandDto,
    LogicTraceWriteResultDto,
    RecordCallSummaryCommandDto,
    RecordTraceArtifactCommandDto,
    StartTraceCommandDto,
)
from veterinary_agent.logic_trace_store.enums import LogicTraceWriteStatus
from veterinary_agent.pet_session_policy import (
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
    PetSessionTraceWriteStatus,
)

TODO_TRACE_STORE_ERROR_CODE = "LOGIC_TRACE_STORE_NOT_IMPLEMENTED"


class LogicTraceStore(Protocol):
    """LogicTraceStore 应用内服务接口契约。"""

    def is_ready(self) -> bool:
        """判断 LogicTraceStore 是否具备基础写入能力。

        :return: 若主存储或可靠降级通道可用，则返回 True。
        """

        ...

    async def start_trace(
        self,
        command: StartTraceCommandDto | AgentTraceStartCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """启动一轮逻辑链。

        :param command: 启动逻辑链的命令 DTO。
        :return: 逻辑链启动写入结果。
        """

        ...

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """追加一条逻辑链事件。

        :param command: 追加逻辑链事件的命令 DTO。
        :return: 逻辑链事件写入结果。
        """

        ...

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一次调用摘要。

        :param command: 记录调用摘要的命令 DTO。
        :return: 调用摘要写入结果。
        """

        ...

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一个 trace artifact。

        :param command: 记录 trace artifact 的命令 DTO。
        :return: artifact 写入结果。
        """

        ...

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto | AgentTraceFinalizeCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """完成一轮逻辑链。

        :param command: 完成逻辑链的命令 DTO。
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

    async def start_trace(
        self,
        command: StartTraceCommandDto | AgentTraceStartCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """返回 Trace 启动降级结果。

        :param command: 启动逻辑链的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 Trace 事件追加降级结果。

        :param command: 追加逻辑链事件的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回调用摘要写入降级结果。

        :param command: 记录调用摘要的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """返回 artifact 写入降级结果。

        :param command: 记录 trace artifact 的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto | AgentTraceFinalizeCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """返回 Trace 完成降级结果。

        :param command: 完成逻辑链的命令 DTO；TODO 空壳不持久化该命令。
        :return: 标记 LogicTraceStore 未接入的降级结果。
        """

        del command
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """返回模型调用摘要写入降级结果。

        :param summary: 脱敏模型调用摘要；TODO 空壳不持久化该摘要。
        :return: 标记 LogicTraceStore 未接入的模型摘要降级结果。
        """

        del summary
        return LlmTraceWriteResultDto(
            status=LlmTraceWriteStatus.DEGRADED,
            reason="LogicTraceStore 模型调用摘要端口尚未接入",
        )

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """返回 AgentRunner 运行摘要写入降级结果。

        :param summary: AgentRunner 脱敏运行摘要；TODO 空壳不持久化该摘要。
        :return: 标记 LogicTraceStore 未接入的运行摘要降级结果。
        """

        del summary
        return AgentRunnerTraceWriteResultDto(
            status=AgentRunnerTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 运行摘要端口尚未接入",
        )

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """返回宠物会话策略摘要写入降级结果。

        :param record: 宠物会话策略判定摘要；TODO 空壳不持久化该摘要。
        :return: 标记 LogicTraceStore 未接入的策略摘要降级结果。
        """

        del record
        return PetSessionTraceWriteResultDto(
            status=PetSessionTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_STORE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 宠物会话摘要端口尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceStore",
    "TODO_TRACE_STORE_ERROR_CODE",
    "TodoLogicTraceStore",
)
