##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/trace.py
# 作用: 定义 PetSessionPolicy 追踪端口、通用 LogicTraceStore 适配器及未接入时的 TODO 空壳。
# 边界: 只转换策略摘要与通用追踪契约；不访问数据库、不实现策略判定或存储细节。
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
from veterinary_agent.pet_session_policy.dto import (
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
)
from veterinary_agent.pet_session_policy.enums import PetSessionTraceWriteStatus

TODO_TRACE_ERROR_CODE = "PET_SESSION_LOGIC_TRACE_STORE_NOT_IMPLEMENTED"


class PetSessionTraceSink(Protocol):
    """宠物会话策略逻辑链摘要写入协议。"""

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """写入宠物会话策略判定摘要。

        :param record: 待写入的宠物会话策略 trace 摘要。
        :return: trace 摘要写入结果。
        """

        ...


class LogicTracePetSessionTraceSink:
    """基于通用 LogicTraceStore 的宠物会话策略追踪适配器。"""

    def __init__(self, store: LogicTraceStore) -> None:
        """初始化宠物会话策略追踪适配器。

        :param store: 负责通用调用摘要持久化的 LogicTraceStore。
        :return: None。
        """

        self._store = store

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """转换并写入一次宠物会话策略判定摘要。

        :param record: PetSessionPolicy 产生的脱敏策略判定摘要。
        :return: PetSessionPolicy 可消费的追踪写入结果。
        """

        result = await self._store.record_call_summary(
            RecordCallSummaryCommandDto(
                call_id=f"{record.trace_id}:pet_session",
                trace_id=record.trace_id,
                request_id=record.request_id,
                call_type=TraceCallType.POLICY_DECISION,
                source_component="PetSessionPolicy",
                provider_ref=record.session_id,
                input_ref=record.requested_pet_id,
                output_ref=record.current_pet_id,
                usage={},
                status=(
                    TraceCallStatus.SUCCEEDED
                    if record.allow_continue
                    else TraceCallStatus.FAILED
                ),
                summary={
                    "schema_version": record.schema_version,
                    "user_id": record.user_id,
                    "session_id": record.session_id,
                    "requested_pet_id": record.requested_pet_id,
                    "current_pet_id": record.current_pet_id,
                    "decision": record.decision.value,
                    "policy_action": record.policy_action.value,
                    "allow_continue": record.allow_continue,
                    "error_code": record.error_code.value
                    if record.error_code is not None
                    else None,
                    "retryable": record.retryable,
                    "missing_field": record.missing_field,
                    "is_new_session": record.is_new_session,
                    "session_status": record.session_status.value
                    if record.session_status is not None
                    else None,
                    "store_error_code": record.store_error_code.value
                    if record.store_error_code is not None
                    else None,
                    "params_version": record.params_version,
                    "config_snapshot_id": record.config_snapshot_id,
                },
                created_at=datetime.now(UTC),
            )
        )
        return PetSessionTraceWriteResultDto(
            status=(
                PetSessionTraceWriteStatus.RECORDED
                if result.status is LogicTraceWriteStatus.WRITTEN
                else PetSessionTraceWriteStatus.DEGRADED
            ),
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


class TodoPetSessionTraceSink:
    """LogicTraceStore 尚未接入时使用的 PetSessionPolicy TODO trace 空壳。"""

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的显式降级结果。

        :param record: 待写入的宠物会话策略 trace 摘要；TODO 空壳不持久化该记录。
        :return: 表示 trace 写入已降级的结果 DTO。
        """

        del record
        return PetSessionTraceWriteResultDto(
            status=PetSessionTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )


__all__: tuple[str, ...] = (
    "TODO_TRACE_ERROR_CODE",
    "LogicTracePetSessionTraceSink",
    "PetSessionTraceSink",
    "TodoPetSessionTraceSink",
)
