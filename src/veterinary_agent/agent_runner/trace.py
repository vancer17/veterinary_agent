##################################################################################################
# 文件: src/veterinary_agent/agent_runner/trace.py
# 作用: 将 AgentRunner 的脱敏运行摘要适配为 LogicTraceStore 通用调用摘要。
# 边界: 只转换 AgentRunner 与通用追踪契约；不访问数据库、不执行 Agent 或模型调用。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.agent_runner.dto import (
    AgentRunSummaryDto,
    AgentRunnerTraceWriteResultDto,
)
from veterinary_agent.agent_runner.enums import (
    AgentRunStatus,
    AgentRunnerTraceWriteStatus,
)
from veterinary_agent.logic_trace_store import (
    LogicTraceStore,
    LogicTraceWriteStatus,
    RecordCallSummaryCommandDto,
    TraceCallStatus,
    TraceCallType,
)


def _to_runner_write_status(
    status: LogicTraceWriteStatus,
) -> AgentRunnerTraceWriteStatus:
    """将通用追踪写入状态转换为 AgentRunner 状态。

    :param status: LogicTraceStore 通用写入状态。
    :return: AgentRunner 运行摘要写入状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return AgentRunnerTraceWriteStatus.DELIVERED
    if status is LogicTraceWriteStatus.SKIPPED:
        return AgentRunnerTraceWriteStatus.SKIPPED
    return AgentRunnerTraceWriteStatus.DEGRADED


class LogicTraceAgentRunnerTraceSink:
    """基于通用 LogicTraceStore 的 AgentRunner 追踪适配器。"""

    def __init__(self, store: LogicTraceStore) -> None:
        """初始化 AgentRunner 追踪适配器。

        :param store: 负责通用调用摘要持久化的 LogicTraceStore。
        :return: None。
        """

        self._store = store

    def is_ready(self) -> bool:
        """判断底层通用追踪存储是否就绪。

        :return: 底层 LogicTraceStore 的就绪状态。
        """

        return self._store.is_ready()

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """转换并写入一次 AgentRunner 运行摘要。

        :param summary: AgentRunner 产生的脱敏运行摘要。
        :return: AgentRunner 可消费的追踪写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=summary.run_id,
                trace_id=summary.trace_id,
                request_id=summary.request_id,
                call_type=TraceCallType.AGENT_RUN,
                source_component="AgentRunner",
                provider_ref=summary.actual_model,
                input_ref=summary.model_profile,
                output_ref=summary.actual_model,
                usage=summary.usage.model_dump(mode="json"),
                status=(
                    TraceCallStatus.SUCCEEDED
                    if summary.status is AgentRunStatus.SUCCEEDED
                    else TraceCallStatus.FAILED
                ),
                summary={
                    "agent_id": summary.agent_id,
                    "agent_version": summary.agent_version,
                    "model_profile": summary.model_profile,
                    "actual_model": summary.actual_model,
                    "status": summary.status.value,
                    "schema_valid": summary.schema_valid,
                    "latency_ms": summary.latency_ms,
                    "retry_count": summary.retry_count,
                    "error_code": summary.error_code.value
                    if summary.error_code is not None
                    else None,
                    "metadata": dict(summary.metadata),
                },
                created_at=datetime.now(UTC),
            )
        )
        return AgentRunnerTraceWriteResultDto(
            status=_to_runner_write_status(result.status),
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


__all__: tuple[str, ...] = ("LogicTraceAgentRunnerTraceSink",)
