##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/trace.py
# 作用: 定义 NonmedicalPetCareAgent trace 端口、LogicTraceStore 适配器和 TODO 空壳。
# 边界: 只转换并写入脱敏非医疗建议摘要，不保存完整草稿正文、不执行逻辑链 schema 投影。
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
from veterinary_agent.nonmedical_pet_care_agent.dto import (
    NonmedicalTraceRecordDto,
    NonmedicalTraceWriteResultDto,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import (
    NonmedicalTraceWriteStatus,
)

TODO_NONMEDICAL_TRACE_ERROR_CODE = "NONMEDICAL_TRACE_STORE_NOT_IMPLEMENTED"


class NonmedicalPetCareTraceSink(Protocol):
    """非医疗养宠脱敏 trace patch 写入端口。"""

    async def write_nonmedical_trace(
        self,
        record: NonmedicalTraceRecordDto,
    ) -> NonmedicalTraceWriteResultDto:
        """写入一次非医疗养宠脱敏 trace patch。

        :param record: 待写入的非医疗 trace 摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceNonmedicalPetCareTraceSink:
    """基于通用 LogicTraceStore 的非医疗养宠 trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 非医疗 trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_nonmedical_trace(
        self,
        record: NonmedicalTraceRecordDto,
    ) -> NonmedicalTraceWriteResultDto:
        """转换并写入一次非医疗 trace 摘要。

        :param record: NonmedicalPetCareAgent 产生的脱敏 trace 摘要。
        :return: NonmedicalPetCareAgent 可消费的 trace 写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:nonmedical:{record.task_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="NonmedicalPetCareAgent",
                provider_ref=record.config_snapshot_id,
                input_ref=record.task_id,
                output_ref=f"{record.task_id}:{record.status.value}",
                usage={
                    "constraint_count": record.constraint_count,
                    "rag_invoked": record.rag_invoked,
                },
                status=TraceCallStatus.SUCCEEDED,
                summary={
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.current_pet_id,
                    "task_id": record.task_id,
                    "segment_type": "nonmedical",
                    "executor_key": "nonmedical_pet_care",
                    "status": record.status.value,
                    "trace_patch": record.trace_patch.model_dump(mode="json"),
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                },
                created_at=datetime.now(UTC),
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = NonmedicalTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = NonmedicalTraceWriteStatus.SKIPPED
        else:
            status = NonmedicalTraceWriteStatus.DEGRADED
        return NonmedicalTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoNonmedicalPetCareTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_nonmedical_trace(
        self,
        record: NonmedicalTraceRecordDto,
    ) -> NonmedicalTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的非医疗摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return NonmedicalTraceWriteResultDto(
            status=NonmedicalTraceWriteStatus.DEGRADED,
            error_code=TODO_NONMEDICAL_TRACE_ERROR_CODE,
            retryable=True,
            detail="NonmedicalPetCareAgent LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceNonmedicalPetCareTraceSink",
    "NonmedicalPetCareTraceSink",
    "TODO_NONMEDICAL_TRACE_ERROR_CODE",
    "TodoNonmedicalPetCareTraceSink",
)
