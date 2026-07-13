##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/trace.py
# 作用: 定义 VetTaskDecomposer trace 端口、LogicTraceStore 适配器和未接入时的 TODO 空壳。
# 边界: 只转换并写入脱敏拆解摘要，不保存用户原文、不保存完整 LLM 输出、不执行任务拆解。
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
from veterinary_agent.vet_task_decomposer.dto import (
    VetTaskDecomposeTraceRecordDto,
    VetTaskTraceWriteResultDto,
)
from veterinary_agent.vet_task_decomposer.enums import VetTaskTraceWriteStatus

TODO_TASK_DECOMPOSER_TRACE_ERROR_CODE = (
    "VET_TASK_DECOMPOSER_TRACE_STORE_NOT_IMPLEMENTED"
)


class VetTaskDecomposerTraceSink(Protocol):
    """任务拆解脱敏摘要写入端口。"""

    async def write_decomposition_summary(
        self,
        record: VetTaskDecomposeTraceRecordDto,
    ) -> VetTaskTraceWriteResultDto:
        """写入一次任务拆解脱敏摘要。

        :param record: 待写入的任务拆解摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceVetTaskDecomposerTraceSink:
    """基于通用 LogicTraceStore 的任务拆解摘要适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 任务拆解摘要适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_decomposition_summary(
        self,
        record: VetTaskDecomposeTraceRecordDto,
    ) -> VetTaskTraceWriteResultDto:
        """转换并写入一次任务拆解摘要。

        :param record: VetTaskDecomposer 产生的脱敏拆解摘要。
        :return: VetTaskDecomposer 可消费的 trace 写入结果。
        """

        summary = record.trace_summary
        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:task_decomposer:{record.run_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="VetTaskDecomposer",
                provider_ref=record.config_snapshot_id,
                input_ref=record.input_text_hash,
                output_ref=f"{record.run_id}:{summary.method.value}",
                usage={
                    "task_count": summary.task_count,
                    "attachment_count": record.attachment_count,
                },
                status=TraceCallStatus.SUCCEEDED,
                summary={
                    "schema_version": record.schema_version,
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.current_pet_id,
                    "decomposer_version": summary.decomposer_version,
                    "method": summary.method.value,
                    "task_count": summary.task_count,
                    "task_types": [task_type.value for task_type in summary.task_types],
                    "llm_unavailable": summary.llm_unavailable,
                    "fallback_used": summary.fallback_used,
                    "confidence": summary.confidence,
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                    "adapter_invoked": True,
                },
                created_at=datetime.now(UTC),
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = VetTaskTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = VetTaskTraceWriteStatus.SKIPPED
        else:
            status = VetTaskTraceWriteStatus.DEGRADED
        return VetTaskTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoVetTaskDecomposerTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_decomposition_summary(
        self,
        record: VetTaskDecomposeTraceRecordDto,
    ) -> VetTaskTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的拆解摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return VetTaskTraceWriteResultDto(
            status=VetTaskTraceWriteStatus.DEGRADED,
            error_code=TODO_TASK_DECOMPOSER_TRACE_ERROR_CODE,
            retryable=True,
            detail="VetTaskDecomposer LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceVetTaskDecomposerTraceSink",
    "TODO_TASK_DECOMPOSER_TRACE_ERROR_CODE",
    "TodoVetTaskDecomposerTraceSink",
    "VetTaskDecomposerTraceSink",
)
