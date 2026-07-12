##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/trace.py
# 作用: 将 AgentApplicationService 的整轮追踪端口适配到通用 LogicTraceStore。
# 边界: 只转换应用层与通用追踪契约；不访问数据库、不执行 Agent 编排或持久化实现细节。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.agent_application_service.dto import (
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
)
from veterinary_agent.agent_application_service.enums import AgentTraceDeliveryStatus
from veterinary_agent.logic_trace_store import (
    FinalizeTraceCommandDto,
    LogicTraceFinalStatus,
    LogicTraceStore,
    LogicTraceWriteResultDto,
    LogicTraceWriteStatus,
    StartTraceCommandDto,
)


def _to_agent_write_result(
    result: LogicTraceWriteResultDto,
) -> AgentTraceWriteResultDto:
    """将通用追踪写入结果转换为应用层交付结果。

    :param result: LogicTraceStore 返回的通用写入结果。
    :return: AgentApplicationService 可消费的追踪交付结果。
    """

    status = (
        AgentTraceDeliveryStatus.WRITTEN
        if result.status is LogicTraceWriteStatus.WRITTEN
        else AgentTraceDeliveryStatus.DEGRADED
    )
    return AgentTraceWriteResultDto(
        status=status,
        error_code=result.error_code,
        retryable=result.retryable,
        detail=result.detail,
    )


class LogicTraceAgentTraceStore:
    """基于通用 LogicTraceStore 的应用层追踪端口适配器。"""

    def __init__(self, store: LogicTraceStore) -> None:
        """初始化应用层追踪适配器。

        :param store: 负责通用逻辑链持久化的 LogicTraceStore。
        :return: None。
        """

        self._store = store

    def is_ready(self) -> bool:
        """判断底层通用追踪存储是否就绪。

        :return: 底层 LogicTraceStore 的就绪状态。
        """

        return self._store.is_ready()

    async def start_trace(
        self,
        command: AgentTraceStartCommandDto,
    ) -> AgentTraceWriteResultDto:
        """将应用层启动命令转换后写入通用追踪存储。

        :param command: AgentApplicationService 的逻辑链启动命令。
        :return: 应用层追踪交付结果。
        """

        result = await self._store.start_trace(
            StartTraceCommandDto.model_validate(command, from_attributes=True)
        )
        return _to_agent_write_result(result)

    async def finalize_trace(
        self,
        command: AgentTraceFinalizeCommandDto,
    ) -> AgentTraceWriteResultDto:
        """将应用层完成命令转换后写入通用追踪存储。

        :param command: AgentApplicationService 的逻辑链完成命令。
        :return: 应用层追踪交付结果。
        """

        result = await self._store.finalize_trace(
            FinalizeTraceCommandDto(
                request_id=command.request_id,
                trace_id=command.trace_id,
                turn_id=command.turn_id,
                run_id=command.run_id,
                final_status=LogicTraceFinalStatus(command.final_status.value),
                user_message_id=command.user_message_id,
                error_code=command.error_code,
                summary=dict(command.summary),
                finalized_at=datetime.now(UTC),
            )
        )
        return _to_agent_write_result(result)


__all__: tuple[str, ...] = ("LogicTraceAgentTraceStore",)
