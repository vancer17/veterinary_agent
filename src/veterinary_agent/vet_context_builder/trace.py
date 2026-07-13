##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/trace.py
# 作用: 定义 VetContextBuilder trace 端口、LogicTraceStore 适配器和未接入时的显式 TODO 空壳。
# 边界: 只转换并写入脱敏构建摘要，不读取上下文来源、不保存完整 prompt 或业务正文。
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
from veterinary_agent.vet_context_builder.dto import (
    ContextTraceRecordDto,
    ContextTraceWriteResultDto,
)
from veterinary_agent.vet_context_builder.enums import ContextTraceWriteStatus

TODO_CONTEXT_TRACE_ERROR_CODE = "VET_CONTEXT_TRACE_STORE_NOT_IMPLEMENTED"


class VetContextTraceSink(Protocol):
    """上下文构建脱敏摘要写入端口。"""

    async def write_context_summary(
        self,
        record: ContextTraceRecordDto,
    ) -> ContextTraceWriteResultDto:
        """写入一次上下文构建脱敏摘要。

        :param record: 待写入的上下文构建摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceVetContextTraceSink:
    """基于通用 LogicTraceStore 的上下文构建摘要适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 上下文摘要适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_context_summary(
        self,
        record: ContextTraceRecordDto,
    ) -> ContextTraceWriteResultDto:
        """转换并写入一次上下文构建摘要。

        :param record: VetContextBuilder 产生的脱敏构建摘要。
        :return: VetContextBuilder 可消费的 trace 写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:context:{record.task_id}",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.GRAPH_EVENT,
                source_component="VetContextBuilder",
                provider_ref=record.config_snapshot_id,
                input_ref=record.task_id,
                output_ref=f"{record.task_id}:{record.status.value}",
                usage={
                    "estimated_context_units": (
                        record.compression_audit.estimated_tokens
                    ),
                    "prompt_block_count": len(
                        record.compression_audit.included_block_ids
                    ),
                },
                status=TraceCallStatus.SUCCEEDED,
                summary={
                    "schema_version": record.schema_version,
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.pet_id,
                    "task_id": record.task_id,
                    "audit_tier": record.audit_tier.value,
                    "generation_profile": (
                        record.generation_profile.value
                        if record.generation_profile is not None
                        else None
                    ),
                    "executor_key": record.executor_key.value,
                    "status": record.status.value,
                    "compression_audit": record.compression_audit.model_dump(
                        mode="json"
                    ),
                    "source_types": [value.value for value in record.source_types],
                    "block_hashes": record.block_hashes,
                    "degraded_reasons": record.degraded_reasons,
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                    "adapter_invoked": True,
                },
                created_at=datetime.now(UTC),
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = ContextTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = ContextTraceWriteStatus.SKIPPED
        else:
            status = ContextTraceWriteStatus.DEGRADED
        return ContextTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoVetContextTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_context_summary(
        self,
        record: ContextTraceRecordDto,
    ) -> ContextTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的构建摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return ContextTraceWriteResultDto(
            status=ContextTraceWriteStatus.DEGRADED,
            error_code=TODO_CONTEXT_TRACE_ERROR_CODE,
            retryable=True,
            detail="VetContextBuilder LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "TODO_CONTEXT_TRACE_ERROR_CODE",
    "LogicTraceVetContextTraceSink",
    "TodoVetContextTraceSink",
    "VetContextTraceSink",
)
