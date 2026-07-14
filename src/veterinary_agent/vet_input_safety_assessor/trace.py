##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/trace.py
# 作用: 定义 VetInputSafetyAssessor trace 端口、LogicTraceStore 适配器和未接入时的 TODO 空壳。
# 边界: 只转换并写入脱敏输入安全摘要，不保存用户原文、不保存完整 LLM 输出、不执行安全评估。
##################################################################################################

from datetime import UTC, datetime
from typing import Protocol

from veterinary_agent.logic_trace_store import (
    LogicTraceStore,
    LogicTraceWriteStatus,
    RecordCallSummaryCommandDto,
    TraceCallStatus,
    TraceCallType,
)
from veterinary_agent.vet_input_safety_assessor.dto import (
    VetInputAssessmentTraceRecordDto,
    VetInputSafetyTraceWriteResultDto,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    VetInputAssessmentTraceWriteStatus,
)

TODO_INPUT_SAFETY_TRACE_ERROR_CODE = "VET_INPUT_SAFETY_TRACE_STORE_NOT_IMPLEMENTED"


class VetInputSafetyTraceSink(Protocol):
    """输入安全评估脱敏摘要写入端口。"""

    async def write_assessment_summary(
        self,
        record: VetInputAssessmentTraceRecordDto,
    ) -> VetInputSafetyTraceWriteResultDto:
        """写入一次输入安全评估脱敏摘要。

        :param record: 待写入的输入安全评估摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceVetInputSafetyTraceSink:
    """基于通用 LogicTraceStore 的输入安全评估摘要适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 输入安全摘要适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_assessment_summary(
        self,
        record: VetInputAssessmentTraceRecordDto,
    ) -> VetInputSafetyTraceWriteResultDto:
        """转换并写入一次输入安全评估摘要。

        :param record: VetInputSafetyAssessor 产生的脱敏评估摘要。
        :return: VetInputSafetyAssessor 可消费的 trace 写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:input_safety:{record.run_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="VetInputSafetyAssessor",
                provider_ref=record.config_snapshot_id,
                input_ref=record.original_user_message_hash,
                output_ref=f"{record.run_id}:input_safety",
                usage={"task_count": len(record.result_summaries)},
                status=TraceCallStatus.SUCCEEDED,
                summary={
                    "schema_version": record.schema_version,
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.current_pet_id,
                    "result_summaries": record.result_summaries,
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                    "adapter_invoked": True,
                },
                created_at=datetime.now(UTC),
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = VetInputAssessmentTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = VetInputAssessmentTraceWriteStatus.SKIPPED
        else:
            status = VetInputAssessmentTraceWriteStatus.DEGRADED
        return VetInputSafetyTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoVetInputSafetyTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_assessment_summary(
        self,
        record: VetInputAssessmentTraceRecordDto,
    ) -> VetInputSafetyTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的输入安全摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return VetInputSafetyTraceWriteResultDto(
            status=VetInputAssessmentTraceWriteStatus.DEGRADED,
            error_code=TODO_INPUT_SAFETY_TRACE_ERROR_CODE,
            retryable=True,
            detail="VetInputSafetyAssessor LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceVetInputSafetyTraceSink",
    "TODO_INPUT_SAFETY_TRACE_ERROR_CODE",
    "TodoVetInputSafetyTraceSink",
    "VetInputSafetyTraceSink",
)
