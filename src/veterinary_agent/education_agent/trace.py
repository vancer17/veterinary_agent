##################################################################################################
# 文件: src/veterinary_agent/education_agent/trace.py
# 作用: 定义 EducationAgent trace 端口、LogicTraceStore 适配器和 TODO 空壳。
# 边界: 只转换并写入脱敏科普摘要，不保存完整草稿正文、不执行逻辑链 schema 投影。
##################################################################################################

from datetime import UTC, datetime
from typing import Protocol

from veterinary_agent.education_agent.dto import (
    EducationTraceRecordDto,
    EducationTraceWriteResultDto,
)
from veterinary_agent.education_agent.enums import EducationTraceWriteStatus
from veterinary_agent.logic_trace_store import (
    LogicTraceStore,
    LogicTraceWriteStatus,
    RecordCallSummaryCommandDto,
    TraceCallStatus,
    TraceCallType,
)

TODO_EDUCATION_TRACE_ERROR_CODE = "EDUCATION_TRACE_STORE_NOT_IMPLEMENTED"


class EducationTraceSink(Protocol):
    """科普脱敏 trace patch 写入端口。"""

    async def write_education_trace(
        self,
        record: EducationTraceRecordDto,
    ) -> EducationTraceWriteResultDto:
        """写入一次科普脱敏 trace patch。

        :param record: 待写入的科普 trace 摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceEducationTraceSink:
    """基于通用 LogicTraceStore 的科普 trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 科普 trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_education_trace(
        self,
        record: EducationTraceRecordDto,
    ) -> EducationTraceWriteResultDto:
        """转换并写入一次科普 trace 摘要。

        :param record: EducationAgent 产生的脱敏 trace 摘要。
        :return: EducationAgent 可消费的 trace 写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:education:{record.task_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="EducationAgent",
                provider_ref=record.config_snapshot_id,
                input_ref=record.task_id,
                output_ref=f"{record.task_id}:{record.status.value}",
                usage={
                    "evidence_binding_count": record.evidence_binding_count,
                    "rag_invoked": record.rag_invoked,
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
            status = EducationTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = EducationTraceWriteStatus.SKIPPED
        else:
            status = EducationTraceWriteStatus.DEGRADED
        return EducationTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoEducationTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_education_trace(
        self,
        record: EducationTraceRecordDto,
    ) -> EducationTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的科普摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return EducationTraceWriteResultDto(
            status=EducationTraceWriteStatus.DEGRADED,
            error_code=TODO_EDUCATION_TRACE_ERROR_CODE,
            retryable=True,
            detail="EducationAgent LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "EducationTraceSink",
    "LogicTraceEducationTraceSink",
    "TODO_EDUCATION_TRACE_ERROR_CODE",
    "TodoEducationTraceSink",
)
