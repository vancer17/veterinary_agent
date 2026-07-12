##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/trace.py
# 作用: 提供 LlmGateway 到通用 LogicTraceStore 的摘要适配器及未接入时的 TODO 空壳。
# 边界: 只转换模型调用摘要与通用追踪契约；不访问数据库、不执行模型调用或业务逻辑。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.llm_gateway.dto import (
    LlmCallSummaryDto,
    LlmTraceWriteResultDto,
)
from veterinary_agent.llm_gateway.enums import LlmTraceWriteStatus
from veterinary_agent.logic_trace_store import (
    LogicTraceStore,
    LogicTraceWriteStatus,
    RecordCallSummaryCommandDto,
    TraceCallStatus,
    TraceCallType,
)


def _to_llm_write_status(status: LogicTraceWriteStatus) -> LlmTraceWriteStatus:
    """将通用追踪写入状态转换为 LlmGateway 状态。

    :param status: LogicTraceStore 通用写入状态。
    :return: LlmGateway 模型调用摘要写入状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return LlmTraceWriteStatus.DELIVERED
    if status is LogicTraceWriteStatus.SKIPPED:
        return LlmTraceWriteStatus.SKIPPED
    return LlmTraceWriteStatus.DEGRADED


class LogicTraceLlmCallTraceStore:
    """基于通用 LogicTraceStore 的模型调用摘要适配器。"""

    def __init__(self, store: LogicTraceStore) -> None:
        """初始化模型调用摘要适配器。

        :param store: 负责通用调用摘要持久化的 LogicTraceStore。
        :return: None。
        """

        self._store = store

    def is_ready(self) -> bool:
        """判断底层通用追踪存储是否就绪。

        :return: 底层 LogicTraceStore 的就绪状态。
        """

        return self._store.is_ready()

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """转换并写入一次脱敏模型调用摘要。

        :param summary: LlmGateway 产生的脱敏模型调用摘要。
        :return: LlmGateway 可消费的摘要写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=summary.call_id,
                trace_id=summary.trace_id,
                request_id=summary.request_id,
                call_type=TraceCallType.MODEL,
                source_component=summary.caller_component,
                provider_ref=summary.provider_route_id,
                input_ref=summary.requested_profile_id,
                output_ref=summary.actual_model,
                usage=summary.usage.model_dump(mode="json"),
                status=(
                    TraceCallStatus.SUCCEEDED
                    if summary.status == "succeeded"
                    else TraceCallStatus.CANCELLED
                    if summary.status == "cancelled"
                    else TraceCallStatus.FAILED
                ),
                summary={
                    "requested_profile_id": summary.requested_profile_id,
                    "actual_profile_id": summary.actual_profile_id,
                    "actual_model": summary.actual_model,
                    "finish_reason": summary.finish_reason.value
                    if summary.finish_reason is not None
                    else None,
                    "latency_ms": summary.latency_ms,
                    "first_token_latency_ms": summary.first_token_latency_ms,
                    "retry_count": summary.retry_count,
                    "fallback_chain": list(summary.fallback_chain),
                    "error_code": summary.error_code.value
                    if summary.error_code is not None
                    else None,
                    "config_snapshot_id": summary.config_snapshot_id,
                },
                created_at=datetime.now(UTC),
            )
        )
        return LlmTraceWriteResultDto(
            status=_to_llm_write_status(result.status),
            reason=result.detail,
        )


class TodoLlmCallTraceStore:
    """LogicTraceStore 尚未接入时使用的模型调用摘要 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 模型调用摘要存储是否就绪。

        :return: 固定返回 False，表示真实 LogicTraceStore 尚未接入。
        """

        return False

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """返回模型调用摘要写入降级状态。

        :param summary: 本次脱敏模型调用摘要。
        :return: 标记 LogicTraceStore 尚未接入的降级结果。
        """

        del summary
        return LlmTraceWriteResultDto(
            status=LlmTraceWriteStatus.DEGRADED,
            reason="LogicTraceStore 模型调用摘要端口尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceLlmCallTraceStore",
    "TodoLlmCallTraceStore",
)
