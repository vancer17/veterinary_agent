##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/trace.py
# 作用: 定义 StandardConsultationAgent trace 端口、LogicTraceStore 适配器和 TODO 空壳。
# 边界: 只转换并写入脱敏标准问诊摘要，不保存完整草稿正文、不执行逻辑链 schema 投影。
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
from veterinary_agent.standard_consultation_agent.dto import (
    StandardConsultationTraceRecordDto,
    StandardTraceWriteResultDto,
)
from veterinary_agent.standard_consultation_agent.enums import StandardTraceWriteStatus

TODO_STANDARD_TRACE_ERROR_CODE = "STANDARD_CONSULTATION_TRACE_STORE_NOT_IMPLEMENTED"


class StandardConsultationTraceSink(Protocol):
    """标准问诊脱敏 trace patch 写入端口。"""

    async def write_standard_trace(
        self,
        record: StandardConsultationTraceRecordDto,
    ) -> StandardTraceWriteResultDto:
        """写入一次标准问诊脱敏 trace patch。

        :param record: 待写入的标准问诊 trace 摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceStandardConsultationTraceSink:
    """基于通用 LogicTraceStore 的标准问诊 trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 标准问诊 trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_standard_trace(
        self,
        record: StandardConsultationTraceRecordDto,
    ) -> StandardTraceWriteResultDto:
        """转换并写入一次标准问诊 trace 摘要。

        :param record: StandardConsultationAgent 产生的脱敏 trace 摘要。
        :return: StandardConsultationAgent 可消费的 trace 写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:standard:{record.task_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="StandardConsultationAgent",
                provider_ref=record.config_snapshot_id,
                input_ref=record.task_id,
                output_ref=f"{record.task_id}:{record.status.value}",
                usage={
                    "selected_question_count": record.selected_question_count,
                    "evidence_binding_count": record.evidence_binding_count,
                },
                status=TraceCallStatus.SUCCEEDED,
                summary={
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.current_pet_id,
                    "task_id": record.task_id,
                    "status": record.status.value,
                    "trace_patch": record.trace_patch.model_dump(mode="json"),
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                },
                created_at=datetime.now(UTC),
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = StandardTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = StandardTraceWriteStatus.SKIPPED
        else:
            status = StandardTraceWriteStatus.DEGRADED
        return StandardTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoStandardConsultationTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_standard_trace(
        self,
        record: StandardConsultationTraceRecordDto,
    ) -> StandardTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的标准问诊摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return StandardTraceWriteResultDto(
            status=StandardTraceWriteStatus.DEGRADED,
            error_code=TODO_STANDARD_TRACE_ERROR_CODE,
            retryable=True,
            detail="StandardConsultationAgent LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceStandardConsultationTraceSink",
    "StandardConsultationTraceSink",
    "TODO_STANDARD_TRACE_ERROR_CODE",
    "TodoStandardConsultationTraceSink",
)
