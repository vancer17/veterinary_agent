##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/trace.py
# 作用: 定义 VetResponseComposer trace 端口、LogicTraceStore 适配器和 TODO 空壳。
# 边界: 只转换并写入回复合成发布摘要，不保存完整 guard 三联稿、不执行 trace schema 校验。
##################################################################################################

from datetime import UTC, datetime
from typing import Protocol

from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    LogicTraceStore,
    LogicTraceWriteStatus,
)
from veterinary_agent.vet_response_composer.dto import (
    ComposerTraceRecordDto,
    ComposerTraceWriteResultDto,
)
from veterinary_agent.vet_response_composer.enums import ComposerTraceWriteStatus

TODO_COMPOSER_TRACE_ERROR_CODE = "COMPOSER_TRACE_STORE_NOT_IMPLEMENTED"


class VetResponseComposerTraceSink(Protocol):
    """回复合成与发布 trace patch 写入端口。"""

    async def write_composer_trace(
        self,
        record: ComposerTraceRecordDto,
    ) -> ComposerTraceWriteResultDto:
        """写入一次 Composer 脱敏 trace patch。

        :param record: 待写入的 Composer trace 摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceVetResponseComposerTraceSink:
    """基于通用 LogicTraceStore 的 Composer trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore Composer trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_composer_trace(
        self,
        record: ComposerTraceRecordDto,
    ) -> ComposerTraceWriteResultDto:
        """转换并写入一次 Composer trace 摘要。

        :param record: VetResponseComposer 产生的脱敏 trace 摘要。
        :return: VetResponseComposer 可消费的 trace 写入结果。
        """

        trace_patch = record.trace_patch.model_dump(mode="json")
        result = await self._store.append_trace_event(
            AppendTraceEventCommandDto(
                request_id=record.request_id,
                trace_id=record.trace_id,
                event_id=f"{record.trace_id}:response_composer:{record.run_id}",
                event_type="vet.response_composer.completed",
                source_component="VetResponseComposer",
                created_at=datetime.now(UTC),
                node_id="vet_response_composer",
                task_id=None,
                segment_id=None,
                input_hash=None,
                output_hash=None,
                summary={
                    "run_id": record.run_id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "pet_id": record.current_pet_id,
                    "published_segment_count": len(
                        record.trace_patch.published_segment_ids
                    ),
                    "turn_audit_tier": record.trace_patch.turn_audit_tier,
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                },
                business_payload={
                    "patch_type": "response_composer",
                    "schema_version": record.trace_schema_version,
                    "capture_policy_version": record.capture_policy_version,
                    "payload": trace_patch,
                },
                schema_ref=record.trace_schema_version,
            )
        )
        if result.status is LogicTraceWriteStatus.WRITTEN:
            status = ComposerTraceWriteStatus.RECORDED
        elif result.status is LogicTraceWriteStatus.SKIPPED:
            status = ComposerTraceWriteStatus.SKIPPED
        else:
            status = ComposerTraceWriteStatus.DEGRADED
        return ComposerTraceWriteResultDto(
            status=status,
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoVetResponseComposerTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_composer_trace(
        self,
        record: ComposerTraceRecordDto,
    ) -> ComposerTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的 Composer 摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return ComposerTraceWriteResultDto(
            status=ComposerTraceWriteStatus.DEGRADED,
            error_code=TODO_COMPOSER_TRACE_ERROR_CODE,
            retryable=True,
            detail="VetResponseComposer LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceVetResponseComposerTraceSink",
    "TODO_COMPOSER_TRACE_ERROR_CODE",
    "TodoVetResponseComposerTraceSink",
    "VetResponseComposerTraceSink",
)
