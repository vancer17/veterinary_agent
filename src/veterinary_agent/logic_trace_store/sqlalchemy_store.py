##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/sqlalchemy_store.py
# 作用: 提供基于 SQLAlchemy 的 LogicTraceStore facade，装配 trace 主记录、事件、调用摘要、
#       artifact、投影与 outbox，并在服务层执行超时预算与统一错误映射。
# 边界: 仅访问项目级 logic trace 表和可选 schema validator；不实现 L2 业务语义、GraphRuntime 或 SSE。
##################################################################################################

import asyncio
import json
from collections.abc import Awaitable, Mapping
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError

from veterinary_agent.agent_application_service import (
    AgentTraceDeliveryStatus,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
)
from veterinary_agent.agent_runner import (
    AgentRunStatus,
    AgentRunSummaryDto,
    AgentRunnerTraceWriteResultDto,
    AgentRunnerTraceWriteStatus,
)
from veterinary_agent.llm_gateway import (
    LlmCallSummaryDto,
    LlmTraceWriteResultDto,
    LlmTraceWriteStatus,
)
from veterinary_agent.logic_trace_store.dto import (
    AppendTraceEventCommandDto,
    BuildTraceProjectionCommandDto,
    FinalizeTraceCommandDto,
    GetTraceQueryDto,
    JsonMap,
    ListTracesQueryDto,
    LogicTraceQueryResultDto,
    LogicTraceSchemaValidationResultDto,
    LogicTraceStoreSettings,
    LogicTraceWriteResultDto,
    RecordCallSummaryCommandDto,
    RecordTraceArtifactCommandDto,
    StartTraceCommandDto,
    TraceArtifactDto,
    TraceCallStatus,
    TraceCallSummaryDto,
    TraceDetailDto,
    TraceDto,
    TraceEventDto,
    TraceOutboxDto,
    TraceProjectionDto,
)
from veterinary_agent.logic_trace_store.enums import (
    LogicTraceErrorCode,
    LogicTraceFinalStatus,
    LogicTraceOperation,
    LogicTraceStatus,
    LogicTraceWriteStatus,
    TraceCallType,
    TraceProjectionType,
)
from veterinary_agent.logic_trace_store.errors import LogicTraceStoreError
from veterinary_agent.logic_trace_store.schema import (
    LogicTraceSchemaValidator,
    TodoLogicTraceSchemaValidator,
)
from veterinary_agent.logic_trace_store.sqlalchemy_tables import (
    LOGIC_TRACE_ARTIFACT_TABLE,
    LOGIC_TRACE_CALL_SUMMARY_TABLE,
    LOGIC_TRACE_EVENT_TABLE,
    LOGIC_TRACE_OUTBOX_TABLE,
    LOGIC_TRACE_PROJECTION_TABLE,
    LOGIC_TRACE_STORE_METADATA,
    LOGIC_TRACE_TABLE,
)
from veterinary_agent.logic_trace_store.store import TodoLogicTraceStore
from veterinary_agent.pet_session_policy import (
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
    PetSessionTraceWriteStatus,
)

_T = TypeVar("_T")


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


def _json_map(value: object) -> JsonMap:
    """将未知映射值转换为字符串键映射。

    :param value: 需要转换的未知值。
    :return: 若输入为映射，则返回字符串键映射；否则返回空映射。
    """

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _json_safe(value: object) -> object:
    """将未知值转换为 JSON 可序列化值。

    :param value: 需要转换的未知值。
    :return: 已通过 JSON 编解码规整后的值。
    :raises TypeError: 当值无法被 JSON 序列化时抛出。
    :raises ValueError: 当值包含 JSON 不支持的浮点特殊值时抛出。
    """

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return json.loads(encoded)


def _json_safe_map(value: Mapping[str, object]) -> JsonMap:
    """将映射转换为 JSON 安全的字符串键映射。

    :param value: 需要转换的映射。
    :return: 已转换为 JSON 安全值的字符串键映射。
    """

    normalized = _json_safe(dict(value))
    if not isinstance(normalized, dict):
        return {}
    return {str(key): item for key, item in normalized.items()}


def _json_int(value: object) -> int:
    """将 JSON 摘要字段收窄为整数。

    :param value: 需要读取的 JSON 字段值。
    :return: 可转换时返回整数值；否则返回 0。
    """

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _json_str_list(value: object) -> list[str]:
    """将 JSON 摘要字段收窄为字符串列表。

    :param value: 需要读取的 JSON 字段值。
    :return: 字符串化后的列表；无法迭代时返回空列表。
    """

    if isinstance(value, str):
        return [value]
    if not isinstance(value, list | tuple | set):
        return []
    return [str(item) for item in value]


def _measure_json_bytes(value: object) -> int:
    """计算 JSON 值序列化后的 UTF-8 字节数。

    :param value: 需要计算大小的 JSON 兼容值。
    :return: 序列化后的 UTF-8 字节数。
    :raises TypeError: 当值无法被 JSON 序列化时抛出。
    :raises ValueError: 当值包含 JSON 不支持的浮点特殊值时抛出。
    """

    encoded = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(encoded.encode("utf-8"))


def _build_logic_trace_store_error(
    *,
    code: LogicTraceErrorCode,
    operation: LogicTraceOperation,
    message: str,
    request_id: str | None,
    trace_id: str | None,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> LogicTraceStoreError:
    """构建 LogicTraceStore 领域错误。

    :param code: LogicTraceStore 稳定错误码。
    :param operation: 当前 LogicTraceStore 操作名。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次逻辑链 ID。
    :param retryable: 可选重试策略覆盖。
    :param conflict_with: 可选冲突对象摘要。
    :return: LogicTraceStore 领域异常对象。
    """

    return LogicTraceStoreError(
        code=code,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=retryable,
        conflict_with=conflict_with,
    )


def _logic_error_to_write_result(
    error: LogicTraceStoreError,
) -> LogicTraceWriteResultDto:
    """将 LogicTraceStore 领域错误转换为通用写入结果。

    :param error: 需要转换的 LogicTraceStore 领域错误。
    :return: 与错误语义对应的通用写入结果。
    """

    if error.retryable:
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.DEGRADED,
            error_code=error.code.value,
            retryable=True,
            detail=error.error.message,
        )
    return LogicTraceWriteResultDto(
        status=LogicTraceWriteStatus.SKIPPED,
        error_code=error.code.value,
        retryable=False,
        detail=error.error.message,
    )


def _map_write_status_for_agent(
    status: LogicTraceWriteStatus,
) -> AgentTraceDeliveryStatus:
    """将 LogicTraceStore 写入状态映射为 AgentTraceDeliveryStatus。

    :param status: LogicTraceStore 通用写入状态。
    :return: 应用层逻辑链交付状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return AgentTraceDeliveryStatus.WRITTEN
    return AgentTraceDeliveryStatus.DEGRADED


def _map_write_status_for_llm(
    status: LogicTraceWriteStatus,
) -> LlmTraceWriteStatus:
    """将 LogicTraceStore 写入状态映射为 LlmTraceWriteStatus。

    :param status: LogicTraceStore 通用写入状态。
    :return: LlmGateway 模型调用摘要写入状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return LlmTraceWriteStatus.DELIVERED
    if status is LogicTraceWriteStatus.SKIPPED:
        return LlmTraceWriteStatus.SKIPPED
    return LlmTraceWriteStatus.DEGRADED


def _map_write_status_for_agent_runner(
    status: LogicTraceWriteStatus,
) -> AgentRunnerTraceWriteStatus:
    """将 LogicTraceStore 写入状态映射为 AgentRunnerTraceWriteStatus。

    :param status: LogicTraceStore 通用写入状态。
    :return: AgentRunner 运行摘要写入状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return AgentRunnerTraceWriteStatus.DELIVERED
    if status is LogicTraceWriteStatus.SKIPPED:
        return AgentRunnerTraceWriteStatus.SKIPPED
    return AgentRunnerTraceWriteStatus.DEGRADED


def _map_write_status_for_pet_session(
    status: LogicTraceWriteStatus,
) -> PetSessionTraceWriteStatus:
    """将 LogicTraceStore 写入状态映射为 PetSessionTraceWriteStatus。

    :param status: LogicTraceStore 通用写入状态。
    :return: PetSessionPolicy 策略摘要写入状态。
    """

    if status is LogicTraceWriteStatus.WRITTEN:
        return PetSessionTraceWriteStatus.RECORDED
    return PetSessionTraceWriteStatus.DEGRADED


def _row_to_trace_dto(row: RowMapping) -> TraceDto:
    """将 logic_trace 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace 行。
    :return: 转换后的 trace DTO。
    """

    return TraceDto.model_validate(dict(row))


def _row_to_event_dto(row: RowMapping) -> TraceEventDto:
    """将 logic_trace_event 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace_event 行。
    :return: 转换后的 trace event DTO。
    """

    return TraceEventDto.model_validate(dict(row))


def _row_to_call_summary_dto(row: RowMapping) -> TraceCallSummaryDto:
    """将 logic_trace_call_summary 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace_call_summary 行。
    :return: 转换后的 call summary DTO。
    """

    return TraceCallSummaryDto.model_validate(dict(row))


def _row_to_artifact_dto(row: RowMapping) -> TraceArtifactDto:
    """将 logic_trace_artifact 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace_artifact 行。
    :return: 转换后的 artifact DTO。
    """

    return TraceArtifactDto.model_validate(dict(row))


def _row_to_projection_dto(row: RowMapping) -> TraceProjectionDto:
    """将 logic_trace_projection 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace_projection 行。
    :return: 转换后的 projection DTO。
    """

    return TraceProjectionDto.model_validate(dict(row))


def _row_to_outbox_dto(row: RowMapping) -> TraceOutboxDto:
    """将 logic_trace_outbox 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 logic_trace_outbox 行。
    :return: 转换后的 outbox DTO。
    """

    return TraceOutboxDto.model_validate(dict(row))


def _trace_summary_base(trace: TraceDto) -> JsonMap:
    """构建 trace 摘要的基础字段。

    :param trace: 当前 trace 主记录 DTO。
    :return: 包含 trace 基础上下文的摘要映射。
    """

    summary = dict(trace.summary)
    summary.update(
        {
            "trace_id": trace.trace_id,
            "request_id": trace.request_id,
            "turn_id": trace.turn_id,
            "run_id": trace.run_id,
            "session_id": trace.session_id,
            "user_id": trace.user_id,
            "pet_id": trace.pet_id,
            "params_version": trace.params_version,
            "config_snapshot_id": trace.config_snapshot_id,
            "status": trace.status.value,
            "final_status": trace.final_status.value
            if trace.final_status is not None
            else None,
        }
    )
    return summary


class SqlAlchemyLogicTraceStore(TodoLogicTraceStore):
    """基于 SQLAlchemy 仓储的 LogicTraceStore 实现。"""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: LogicTraceStoreSettings | None = None,
        schema_validator: LogicTraceSchemaValidator | None = None,
    ) -> None:
        """初始化 SQLAlchemy LogicTraceStore。

        :param engine: SQLAlchemy 数据库引擎。
        :param settings: 可选 LogicTraceStore 运行配置。
        :param schema_validator: 可选业务 trace patch 校验器。
        :return: None。
        """

        self._engine = engine
        self._settings = settings if settings is not None else LogicTraceStoreSettings()
        self._schema_validator = (
            schema_validator
            if schema_validator is not None
            else TodoLogicTraceSchemaValidator()
        )

    def dispose(self) -> None:
        """释放 LogicTraceStore 持有的底层数据库资源。

        :return: None。
        """

        self._engine.dispose()

    def is_ready(self) -> bool:
        """判断 LogicTraceStore 是否具备基础写入能力。

        :return: 若数据库连接可用，则返回 True。
        """

        try:
            with self._engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except Exception:
            return False
        return True

    async def _run_with_timeout(
        self,
        *,
        operation: LogicTraceOperation,
        request_id: str | None,
        trace_id: str | None,
        awaitable: Awaitable[_T],
    ) -> _T:
        """按 RuntimeConfig 预算等待异步调用完成。

        :param operation: 当前 LogicTraceStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param awaitable: 需要受超时预算约束的异步调用。
        :return: 异步调用返回值。
        :raises LogicTraceStoreError: 当等待超过配置的操作超时时间时抛出。
        """

        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=self._settings.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            raise _build_logic_trace_store_error(
                code=LogicTraceErrorCode.TRACE_OPERATION_TIMEOUT,
                operation=operation,
                message="LogicTraceStore 操作超过 RuntimeConfig 配置的超时预算",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={
                    "operation_timeout_seconds": self._settings.operation_timeout_seconds,
                },
            ) from exc

    async def _execute_write_operation(
        self,
        *,
        operation: LogicTraceOperation,
        request_id: str | None,
        trace_id: str | None,
        awaitable: Awaitable[LogicTraceWriteResultDto],
    ) -> LogicTraceWriteResultDto:
        """执行写入操作并统一映射领域错误。

        :param operation: 当前 LogicTraceStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param awaitable: 返回通用写入结果的异步调用。
        :return: 写入成功、跳过或降级结果。
        """

        try:
            return await self._run_with_timeout(
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
                awaitable=awaitable,
            )
        except LogicTraceStoreError as exc:
            return _logic_error_to_write_result(exc)
        except ValidationError as exc:
            return LogicTraceWriteResultDto(
                status=LogicTraceWriteStatus.SKIPPED,
                error_code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT.value,
                retryable=False,
                detail=str(exc),
            )
        except SQLAlchemyError as exc:
            return LogicTraceWriteResultDto(
                status=LogicTraceWriteStatus.DEGRADED,
                error_code=LogicTraceErrorCode.TRACE_STORAGE_WRITE_FAILED.value,
                retryable=True,
                detail=type(exc).__name__,
            )
        except Exception as exc:
            return LogicTraceWriteResultDto(
                status=LogicTraceWriteStatus.DEGRADED,
                error_code=LogicTraceErrorCode.TRACE_STORAGE_WRITE_FAILED.value,
                retryable=True,
                detail=type(exc).__name__,
            )

    def _ensure_trace_exists(
        self,
        *,
        connection: Connection,
        trace_id: str,
        operation: LogicTraceOperation,
        request_id: str | None,
    ) -> TraceDto:
        """读取指定 trace 并在不存在时抛出领域错误。

        :param connection: 当前数据库连接。
        :param trace_id: 需要读取的逻辑链 ID。
        :param operation: 当前 LogicTraceStore 操作名。
        :param request_id: 本次请求 ID。
        :return: 命中的 trace 主记录 DTO。
        :raises LogicTraceStoreError: 当 trace 不存在时抛出。
        """

        row = (
            connection.execute(
                select(LOGIC_TRACE_TABLE).where(
                    LOGIC_TRACE_TABLE.c.trace_id == trace_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _build_logic_trace_store_error(
                code=LogicTraceErrorCode.TRACE_NOT_FOUND,
                operation=operation,
                message="logic trace 不存在",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"trace_id": trace_id},
            )
        return _row_to_trace_dto(row)

    def _assert_trace_writable(
        self,
        *,
        trace: TraceDto,
        operation: LogicTraceOperation,
        request_id: str | None,
    ) -> None:
        """确认 trace 仍处于可写状态。

        :param trace: 当前 trace 主记录 DTO。
        :param operation: 当前 LogicTraceStore 操作名。
        :param request_id: 本次请求 ID。
        :return: None。
        :raises LogicTraceStoreError: 当 trace 已完结时抛出。
        """

        if trace.status is LogicTraceStatus.FINALIZED:
            raise _build_logic_trace_store_error(
                code=LogicTraceErrorCode.TRACE_ALREADY_FINALIZED,
                operation=operation,
                message="logic trace 已完结后不允许继续写入",
                request_id=request_id,
                trace_id=trace.trace_id,
                retryable=False,
                conflict_with={
                    "trace_id": trace.trace_id,
                    "final_status": trace.final_status.value
                    if trace.final_status
                    else None,
                },
            )

    def _trace_summary_after_event(
        self,
        *,
        trace: TraceDto,
        event: TraceEventDto,
    ) -> JsonMap:
        """根据新事件生成 trace 摘要。

        :param trace: 当前 trace 主记录 DTO。
        :param event: 新写入的 trace event DTO。
        :return: 更新后的 trace 摘要。
        """

        summary = _trace_summary_base(trace)
        summary.update(
            {
                "event_count": _json_int(summary.get("event_count")) + 1,
                "call_summary_count": _json_int(summary.get("call_summary_count")),
                "artifact_count": _json_int(summary.get("artifact_count")),
                "projection_count": _json_int(summary.get("projection_count")),
                "last_event_id": event.event_id,
                "last_event_type": event.event_type,
                "last_event_at": event.created_at.isoformat(),
            }
        )
        if event.business_payload:
            summary["last_business_payload_keys"] = sorted(event.business_payload)
        if event.segment_id is not None:
            summary["last_segment_id"] = event.segment_id
        if event.schema_ref is not None:
            summary["last_schema_ref"] = event.schema_ref
        degraded_flags = _json_str_list(event.summary.get("degraded_flags"))
        if degraded_flags:
            summary["degraded_flags"] = degraded_flags
        return summary

    def _trace_summary_after_call(
        self,
        *,
        trace: TraceDto,
        call_summary: TraceCallSummaryDto,
    ) -> JsonMap:
        """根据新调用摘要生成 trace 摘要。

        :param trace: 当前 trace 主记录 DTO。
        :param call_summary: 新写入的调用摘要 DTO。
        :return: 更新后的 trace 摘要。
        """

        summary = _trace_summary_base(trace)
        summary.update(
            {
                "event_count": _json_int(summary.get("event_count")),
                "call_summary_count": _json_int(summary.get("call_summary_count")) + 1,
                "artifact_count": _json_int(summary.get("artifact_count")),
                "projection_count": _json_int(summary.get("projection_count")),
                "last_call_id": call_summary.call_id,
                "last_call_type": call_summary.call_type.value,
                "last_call_status": call_summary.status.value,
                "last_call_at": call_summary.created_at.isoformat(),
            }
        )
        return summary

    def _trace_summary_after_artifact(
        self,
        *,
        trace: TraceDto,
        artifact: TraceArtifactDto,
    ) -> JsonMap:
        """根据新 artifact 生成 trace 摘要。

        :param trace: 当前 trace 主记录 DTO。
        :param artifact: 新写入的 artifact DTO。
        :return: 更新后的 trace 摘要。
        """

        summary = _trace_summary_base(trace)
        summary.update(
            {
                "event_count": _json_int(summary.get("event_count")),
                "call_summary_count": _json_int(summary.get("call_summary_count")),
                "artifact_count": _json_int(summary.get("artifact_count")) + 1,
                "projection_count": _json_int(summary.get("projection_count")),
                "last_artifact_id": artifact.artifact_id,
                "last_artifact_type": artifact.artifact_type.value,
                "last_artifact_at": artifact.created_at.isoformat(),
            }
        )
        return summary

    def _trace_summary_after_projection(
        self,
        *,
        trace: TraceDto,
        projection: TraceProjectionDto,
    ) -> JsonMap:
        """根据新投影生成 trace 摘要。

        :param trace: 当前 trace 主记录 DTO。
        :param projection: 新写入或更新的投影 DTO。
        :return: 更新后的 trace 摘要。
        """

        summary = _trace_summary_base(trace)
        summary.update(
            {
                "event_count": _json_int(summary.get("event_count")),
                "call_summary_count": _json_int(summary.get("call_summary_count")),
                "artifact_count": _json_int(summary.get("artifact_count")),
                "projection_count": _json_int(summary.get("projection_count")) + 1,
                "last_projection_id": projection.projection_id,
                "last_projection_type": projection.projection_type.value,
                "last_projection_at": projection.updated_at.isoformat(),
            }
        )
        return summary

    def _insert_outbox_record(
        self,
        *,
        connection: Connection,
        trace_id: str,
        event_kind: str,
        payload: JsonMap,
    ) -> TraceOutboxDto:
        """插入一条 outbox 记录。

        :param connection: 当前数据库连接。
        :param trace_id: 关联的逻辑链 ID。
        :param event_kind: outbox 事件类型。
        :param payload: 待补偿事件负载。
        :return: 已写入的 outbox DTO。
        """

        outbox_id = f"trace_outbox_{uuid4().hex}"
        now = _now_utc()
        connection.execute(
            insert(LOGIC_TRACE_OUTBOX_TABLE).values(
                outbox_id=outbox_id,
                trace_id=trace_id,
                event_kind=event_kind,
                payload=payload,
                status="pending",
                retry_count=0,
                next_retry_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        row = (
            connection.execute(
                select(LOGIC_TRACE_OUTBOX_TABLE).where(
                    LOGIC_TRACE_OUTBOX_TABLE.c.outbox_id == outbox_id
                )
            )
            .mappings()
            .one()
        )
        return _row_to_outbox_dto(row)

    def _start_trace_sync(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """同步启动一轮逻辑链。

        :param command: 启动逻辑链的命令 DTO。
        :return: Trace 启动写入结果。
        :raises LogicTraceStoreError: 当 trace 已存在且上下文冲突时抛出。
        """

        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(LOGIC_TRACE_TABLE).where(
                        LOGIC_TRACE_TABLE.c.trace_id == command.trace_id
                    )
                )
                .mappings()
                .first()
            )
            now = _now_utc()
            if row is not None:
                existing = _row_to_trace_dto(row)
                if (
                    existing.request_id == command.request_id
                    and existing.run_id == command.run_id
                    and existing.idempotency_key == command.idempotency_key
                ):
                    return LogicTraceWriteResultDto(
                        status=LogicTraceWriteStatus.WRITTEN,
                        retryable=False,
                        detail="logic trace start 幂等命中",
                        idempotent=True,
                    )
                if existing.status is LogicTraceStatus.FINALIZED:
                    raise _build_logic_trace_store_error(
                        code=LogicTraceErrorCode.TRACE_ALREADY_FINALIZED,
                        operation=LogicTraceOperation.START_TRACE,
                        message="logic trace 已完结后不允许重复启动",
                        request_id=command.request_id,
                        trace_id=command.trace_id,
                        retryable=False,
                        conflict_with={"trace_id": command.trace_id},
                    )
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT,
                    operation=LogicTraceOperation.START_TRACE,
                    message="logic trace 已存在且与当前启动命令不一致",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={
                        "trace_id": command.trace_id,
                        "request_id": existing.request_id,
                        "run_id": existing.run_id,
                    },
                )
            trace_summary = {
                "event_count": 0,
                "call_summary_count": 0,
                "artifact_count": 0,
                "projection_count": 0,
                "degraded_flags": [],
                "write_state": LogicTraceStatus.OPEN.value,
            }
            connection.execute(
                insert(LOGIC_TRACE_TABLE).values(
                    trace_id=command.trace_id,
                    request_id=command.request_id,
                    turn_id=command.turn_id,
                    run_id=command.run_id,
                    session_id=command.session_id,
                    user_id=command.user_id,
                    pet_id=command.pet_id,
                    params_version=command.params_version,
                    config_snapshot_id=command.config_snapshot_id,
                    idempotency_key=command.idempotency_key,
                    capture_policy_ref=command.capture_policy_ref,
                    status=LogicTraceStatus.OPEN.value,
                    final_status=None,
                    user_message_id=None,
                    error_code=None,
                    summary=trace_summary,
                    started_at=now,
                    finalized_at=None,
                    updated_at=now,
                )
            )
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.WRITTEN,
            retryable=False,
            detail="logic trace 已启动",
        )

    def _append_trace_event_sync(
        self,
        command: AppendTraceEventCommandDto,
        validation_result: LogicTraceSchemaValidationResultDto,
    ) -> LogicTraceWriteResultDto:
        """同步追加逻辑链事件。

        :param command: 追加逻辑链事件的命令 DTO。
        :param validation_result: schema 校验或 TODO 透传结果。
        :return: Trace 事件写入结果。
        :raises LogicTraceStoreError: 当 trace 不存在、已完结或 payload 超限时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=command.trace_id,
                operation=LogicTraceOperation.APPEND_TRACE_EVENT,
                request_id=command.request_id,
            )
            self._assert_trace_writable(
                trace=trace,
                operation=LogicTraceOperation.APPEND_TRACE_EVENT,
                request_id=command.request_id,
            )
            event_count = connection.execute(
                select(func.count())
                .select_from(LOGIC_TRACE_EVENT_TABLE)
                .where(LOGIC_TRACE_EVENT_TABLE.c.trace_id == command.trace_id)
            ).scalar_one()
            if int(event_count) >= self._settings.max_trace_events:
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT,
                    operation=LogicTraceOperation.APPEND_TRACE_EVENT,
                    message="logic trace 事件数超过上限",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={"max_trace_events": self._settings.max_trace_events},
                )
            event = TraceEventDto(
                event_id=command.event_id,
                trace_id=command.trace_id,
                request_id=command.request_id,
                event_type=command.event_type,
                source_component=command.source_component,
                node_id=command.node_id,
                task_id=command.task_id,
                segment_id=command.segment_id,
                input_hash=command.input_hash,
                output_hash=command.output_hash,
                summary=_json_safe_map(
                    {
                        **dict(command.summary),
                        "degraded_flags": list(validation_result.degraded_flags),
                    }
                ),
                business_payload=_json_safe_map(
                    validation_result.normalized_business_payload
                ),
                schema_ref=validation_result.schema_ref,
                created_at=command.created_at,
            )
            if (
                _measure_json_bytes(
                    {
                        "summary": event.summary,
                        "business_payload": event.business_payload,
                    }
                )
                > self._settings.max_event_payload_bytes
            ):
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT,
                    operation=LogicTraceOperation.APPEND_TRACE_EVENT,
                    message="logic trace event payload 超过上限",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={
                        "max_event_payload_bytes": self._settings.max_event_payload_bytes
                    },
                )
            existing = (
                connection.execute(
                    select(LOGIC_TRACE_EVENT_TABLE).where(
                        LOGIC_TRACE_EVENT_TABLE.c.event_id == event.event_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return LogicTraceWriteResultDto(
                    status=LogicTraceWriteStatus.WRITTEN,
                    retryable=False,
                    detail="logic trace event 幂等命中",
                    idempotent=True,
                )
            connection.execute(
                insert(LOGIC_TRACE_EVENT_TABLE).values(
                    event_id=event.event_id,
                    trace_id=event.trace_id,
                    request_id=event.request_id,
                    event_type=event.event_type,
                    source_component=event.source_component,
                    node_id=event.node_id,
                    task_id=event.task_id,
                    segment_id=event.segment_id,
                    input_hash=event.input_hash,
                    output_hash=event.output_hash,
                    summary=event.summary,
                    business_payload=event.business_payload,
                    schema_ref=event.schema_ref,
                    created_at=event.created_at,
                )
            )
            updated_summary = _json_safe_map(
                self._trace_summary_after_event(trace=trace, event=event)
            )
            connection.execute(
                update(LOGIC_TRACE_TABLE)
                .where(LOGIC_TRACE_TABLE.c.trace_id == trace.trace_id)
                .values(summary=updated_summary, updated_at=command.created_at)
            )
            if validation_result.degraded_flags:
                self._insert_outbox_record(
                    connection=connection,
                    trace_id=trace.trace_id,
                    event_kind="trace_schema_degraded",
                    payload={
                        "trace_id": trace.trace_id,
                        "event_id": event.event_id,
                        "degraded_flags": list(validation_result.degraded_flags),
                    },
                )
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.WRITTEN,
            retryable=False,
            detail="logic trace event 已追加",
        )

    def _record_call_summary_sync(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """同步记录调用摘要。

        :param command: 记录调用摘要的命令 DTO。
        :return: 调用摘要写入结果。
        :raises LogicTraceStoreError: 当 trace 不存在、已完结或 payload 超限时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=command.trace_id,
                operation=LogicTraceOperation.RECORD_CALL_SUMMARY,
                request_id=command.request_id,
            )
            self._assert_trace_writable(
                trace=trace,
                operation=LogicTraceOperation.RECORD_CALL_SUMMARY,
                request_id=command.request_id,
            )
            call_summary = TraceCallSummaryDto(
                call_id=command.call_id,
                trace_id=command.trace_id,
                request_id=command.request_id,
                call_type=command.call_type,
                source_component=command.source_component,
                provider_ref=command.provider_ref,
                input_ref=command.input_ref,
                output_ref=command.output_ref,
                usage=dict(command.usage),
                status=command.status,
                summary=_json_safe_map(command.summary),
                created_at=command.created_at,
            )
            call_summary = call_summary.model_copy(
                update={"usage": _json_safe_map(command.usage)}
            )
            if (
                _measure_json_bytes(
                    {"usage": call_summary.usage, "summary": call_summary.summary}
                )
                > self._settings.max_call_summary_bytes
            ):
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT,
                    operation=LogicTraceOperation.RECORD_CALL_SUMMARY,
                    message="logic trace 调用摘要 payload 超过上限",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={
                        "max_call_summary_bytes": self._settings.max_call_summary_bytes
                    },
                )
            existing = (
                connection.execute(
                    select(LOGIC_TRACE_CALL_SUMMARY_TABLE).where(
                        LOGIC_TRACE_CALL_SUMMARY_TABLE.c.call_id == call_summary.call_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return LogicTraceWriteResultDto(
                    status=LogicTraceWriteStatus.WRITTEN,
                    retryable=False,
                    detail="logic trace 调用摘要幂等命中",
                    idempotent=True,
                )
            connection.execute(
                insert(LOGIC_TRACE_CALL_SUMMARY_TABLE).values(
                    call_id=call_summary.call_id,
                    trace_id=call_summary.trace_id,
                    request_id=call_summary.request_id,
                    call_type=call_summary.call_type.value,
                    source_component=call_summary.source_component,
                    provider_ref=call_summary.provider_ref,
                    input_ref=call_summary.input_ref,
                    output_ref=call_summary.output_ref,
                    usage=call_summary.usage,
                    status=call_summary.status.value,
                    summary=call_summary.summary,
                    created_at=call_summary.created_at,
                )
            )
            updated_summary = _json_safe_map(
                self._trace_summary_after_call(
                    trace=trace,
                    call_summary=call_summary,
                )
            )
            connection.execute(
                update(LOGIC_TRACE_TABLE)
                .where(LOGIC_TRACE_TABLE.c.trace_id == trace.trace_id)
                .values(summary=updated_summary, updated_at=command.created_at)
            )
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.WRITTEN,
            retryable=False,
            detail="logic trace 调用摘要已写入",
        )

    def _record_trace_artifact_sync(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """同步记录 trace artifact。

        :param command: 记录 trace artifact 的命令 DTO。
        :return: artifact 写入结果。
        :raises LogicTraceStoreError: 当 trace 不存在、已完结或 payload 超限时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=command.trace_id,
                operation=LogicTraceOperation.RECORD_TRACE_ARTIFACT,
                request_id=None,
            )
            self._assert_trace_writable(
                trace=trace,
                operation=LogicTraceOperation.RECORD_TRACE_ARTIFACT,
                request_id=None,
            )
            artifact = TraceArtifactDto(
                artifact_id=command.artifact_id,
                trace_id=command.trace_id,
                artifact_type=command.artifact_type,
                storage_ref=command.storage_ref,
                content_hash=command.content_hash,
                visibility_policy=command.visibility_policy,
                metadata=_json_safe_map(command.metadata),
                created_at=command.created_at,
            )
            if (
                _measure_json_bytes(
                    {
                        "storage_ref": artifact.storage_ref,
                        "content_hash": artifact.content_hash,
                        "visibility_policy": artifact.visibility_policy,
                        "metadata": artifact.metadata,
                    }
                )
                > self._settings.max_artifact_ref_bytes
            ):
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_INVALID_ARGUMENT,
                    operation=LogicTraceOperation.RECORD_TRACE_ARTIFACT,
                    message="logic trace artifact 引用 payload 超过上限",
                    request_id=None,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={
                        "max_artifact_ref_bytes": self._settings.max_artifact_ref_bytes
                    },
                )
            existing = (
                connection.execute(
                    select(LOGIC_TRACE_ARTIFACT_TABLE).where(
                        LOGIC_TRACE_ARTIFACT_TABLE.c.artifact_id == artifact.artifact_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return LogicTraceWriteResultDto(
                    status=LogicTraceWriteStatus.WRITTEN,
                    retryable=False,
                    detail="logic trace artifact 幂等命中",
                    idempotent=True,
                )
            connection.execute(
                insert(LOGIC_TRACE_ARTIFACT_TABLE).values(
                    artifact_id=artifact.artifact_id,
                    trace_id=artifact.trace_id,
                    artifact_type=artifact.artifact_type.value,
                    storage_ref=artifact.storage_ref,
                    content_hash=artifact.content_hash,
                    visibility_policy=artifact.visibility_policy,
                    metadata=artifact.metadata,
                    created_at=artifact.created_at,
                )
            )
            updated_summary = _json_safe_map(
                self._trace_summary_after_artifact(
                    trace=trace,
                    artifact=artifact,
                )
            )
            connection.execute(
                update(LOGIC_TRACE_TABLE)
                .where(LOGIC_TRACE_TABLE.c.trace_id == trace.trace_id)
                .values(summary=updated_summary, updated_at=command.created_at)
            )
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.WRITTEN,
            retryable=False,
            detail="logic trace artifact 已写入",
        )

    def _finalize_trace_sync(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """同步完成逻辑链。

        :param command: 完成逻辑链的命令 DTO。
        :return: Trace 完成写入结果。
        :raises LogicTraceStoreError: 当 trace 不存在或已以不同结果完结时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=command.trace_id,
                operation=LogicTraceOperation.FINALIZE_TRACE,
                request_id=command.request_id,
            )
            if trace.status is LogicTraceStatus.FINALIZED:
                if (
                    trace.final_status is not None
                    and trace.final_status.value == command.final_status.value
                    and trace.user_message_id == command.user_message_id
                    and trace.error_code == command.error_code
                ):
                    return LogicTraceWriteResultDto(
                        status=LogicTraceWriteStatus.WRITTEN,
                        retryable=False,
                        detail="logic trace finalize 幂等命中",
                        idempotent=True,
                    )
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_ALREADY_FINALIZED,
                    operation=LogicTraceOperation.FINALIZE_TRACE,
                    message="logic trace 已完结后不允许重复完成",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={"trace_id": command.trace_id},
                )
            summary = _trace_summary_base(trace)
            summary.update(
                {
                    "event_count": _json_int(summary.get("event_count")),
                    "call_summary_count": _json_int(summary.get("call_summary_count")),
                    "artifact_count": _json_int(summary.get("artifact_count")),
                    "projection_count": _json_int(summary.get("projection_count")),
                    "final_status": command.final_status.value,
                    "user_message_id": command.user_message_id,
                    "error_code": command.error_code,
                    "finalized_reason": _json_safe_map(command.summary),
                }
            )
            summary = _json_safe_map(summary)
            connection.execute(
                update(LOGIC_TRACE_TABLE)
                .where(LOGIC_TRACE_TABLE.c.trace_id == trace.trace_id)
                .values(
                    status=LogicTraceStatus.FINALIZED.value,
                    final_status=command.final_status.value,
                    user_message_id=command.user_message_id,
                    error_code=command.error_code,
                    summary=summary,
                    finalized_at=command.finalized_at,
                    updated_at=command.finalized_at,
                )
            )
        return LogicTraceWriteResultDto(
            status=LogicTraceWriteStatus.WRITTEN,
            retryable=False,
            detail="logic trace 已完成",
        )

    def _build_projection_sync(
        self,
        command: BuildTraceProjectionCommandDto,
    ) -> TraceProjectionDto:
        """同步构建逻辑链投影。

        :param command: 构建逻辑链投影的命令 DTO。
        :return: 已写入或更新的投影 DTO。
        :raises LogicTraceStoreError: 当 trace 不存在、投影缺少安全负载或 payload 超限时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=command.trace_id,
                operation=LogicTraceOperation.BUILD_TRACE_PROJECTION,
                request_id=command.request_id,
            )
            events = (
                connection.execute(
                    select(LOGIC_TRACE_EVENT_TABLE)
                    .where(LOGIC_TRACE_EVENT_TABLE.c.trace_id == command.trace_id)
                    .order_by(LOGIC_TRACE_EVENT_TABLE.c.created_at.asc())
                )
                .mappings()
                .all()
            )
            call_summaries = (
                connection.execute(
                    select(LOGIC_TRACE_CALL_SUMMARY_TABLE)
                    .where(
                        LOGIC_TRACE_CALL_SUMMARY_TABLE.c.trace_id == command.trace_id
                    )
                    .order_by(LOGIC_TRACE_CALL_SUMMARY_TABLE.c.created_at.asc())
                )
                .mappings()
                .all()
            )
            artifacts = (
                connection.execute(
                    select(LOGIC_TRACE_ARTIFACT_TABLE)
                    .where(LOGIC_TRACE_ARTIFACT_TABLE.c.trace_id == command.trace_id)
                    .order_by(LOGIC_TRACE_ARTIFACT_TABLE.c.created_at.asc())
                )
                .mappings()
                .all()
            )
            if command.projection_type is TraceProjectionType.REASONING_DISPLAY:
                display_payload = dict(command.display_payload)
                if not display_payload:
                    summary_text = trace.summary.get("reasoning_display")
                    if isinstance(summary_text, Mapping):
                        display_payload = _json_map(summary_text)
                    else:
                        candidate_text = trace.summary.get("reasoning_display_text")
                        if isinstance(candidate_text, str) and candidate_text.strip():
                            display_payload = {
                                "projection_id": f"rdp_{trace.trace_id}",
                                "trace_id": trace.trace_id,
                                "segment_id": command.segment_id,
                                "title": trace.summary.get("reasoning_display_title")
                                or "处理过程",
                                "text": candidate_text,
                                "metadata": trace.summary.get(
                                    "reasoning_display_metadata", {}
                                ),
                            }
                if not display_payload:
                    raise _build_logic_trace_store_error(
                        code=LogicTraceErrorCode.TRACE_PROJECTION_BUILD_FAILED,
                        operation=LogicTraceOperation.BUILD_TRACE_PROJECTION,
                        message="未提供可展示 reasoning display 的文本或负载",
                        request_id=command.request_id,
                        trace_id=command.trace_id,
                        retryable=False,
                        conflict_with={
                            "projection_type": command.projection_type.value
                        },
                    )
            elif command.projection_type is TraceProjectionType.TIMELINE:
                display_payload = {
                    "trace": trace.model_dump(mode="json"),
                    "events": [
                        _row_to_event_dto(event).model_dump(mode="json")
                        for event in events
                    ],
                    "call_summaries": [
                        _row_to_call_summary_dto(call_summary).model_dump(mode="json")
                        for call_summary in call_summaries
                    ],
                    "artifacts": [
                        _row_to_artifact_dto(artifact).model_dump(mode="json")
                        for artifact in artifacts
                    ],
                }
            elif command.projection_type is TraceProjectionType.DECISION:
                display_payload = {
                    "trace": trace.model_dump(mode="json"),
                    "summary": dict(trace.summary),
                    "final_status": trace.final_status.value
                    if trace.final_status is not None
                    else None,
                    "last_events": [
                        _row_to_event_dto(event).model_dump(mode="json")
                        for event in events[-10:]
                    ],
                }
            else:
                display_payload = {
                    "trace": trace.model_dump(mode="json"),
                    "artifacts": [
                        _row_to_artifact_dto(artifact).model_dump(mode="json")
                        for artifact in artifacts
                    ],
                }
            projection_id = (
                f"{command.projection_type.value}:{command.trace_id}:{command.version}"
            )
            now = _now_utc()
            display_payload = _json_safe_map(display_payload)
            if (
                _measure_json_bytes(display_payload)
                > self._settings.max_projection_bytes
            ):
                raise _build_logic_trace_store_error(
                    code=LogicTraceErrorCode.TRACE_PROJECTION_BUILD_FAILED,
                    operation=LogicTraceOperation.BUILD_TRACE_PROJECTION,
                    message="logic trace projection payload 超过上限",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=False,
                    conflict_with={
                        "max_projection_bytes": self._settings.max_projection_bytes
                    },
                )
            existing = (
                connection.execute(
                    select(LOGIC_TRACE_PROJECTION_TABLE).where(
                        LOGIC_TRACE_PROJECTION_TABLE.c.projection_id == projection_id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                existing_projection = _row_to_projection_dto(existing)
                if existing_projection.view_payload == display_payload:
                    return existing_projection
            projection = TraceProjectionDto(
                projection_id=projection_id,
                trace_id=trace.trace_id,
                projection_type=command.projection_type,
                version=command.version,
                view_payload=display_payload,
                created_at=now,
                updated_at=now,
            )
            if existing is None:
                connection.execute(
                    insert(LOGIC_TRACE_PROJECTION_TABLE).values(
                        projection_id=projection.projection_id,
                        trace_id=projection.trace_id,
                        projection_type=projection.projection_type.value,
                        version=projection.version,
                        view_payload=projection.view_payload,
                        updated_at=projection.updated_at,
                        created_at=projection.updated_at,
                    )
                )
            else:
                connection.execute(
                    update(LOGIC_TRACE_PROJECTION_TABLE)
                    .where(
                        LOGIC_TRACE_PROJECTION_TABLE.c.projection_id == projection_id
                    )
                    .values(
                        trace_id=projection.trace_id,
                        projection_type=projection.projection_type.value,
                        version=projection.version,
                        view_payload=projection.view_payload,
                        updated_at=projection.updated_at,
                    )
                )
            updated_summary = _json_safe_map(
                self._trace_summary_after_projection(
                    trace=trace,
                    projection=projection,
                )
            )
            connection.execute(
                update(LOGIC_TRACE_TABLE)
                .where(LOGIC_TRACE_TABLE.c.trace_id == trace.trace_id)
                .values(summary=updated_summary, updated_at=now)
            )
        return projection

    def _get_trace_sync(self, query: GetTraceQueryDto) -> TraceDetailDto:
        """同步读取逻辑链详情。

        :param query: 查询单条逻辑链的查询 DTO。
        :return: 逻辑链详情 DTO。
        :raises LogicTraceStoreError: 当 trace 不存在时抛出。
        """

        with self._engine.begin() as connection:
            trace = self._ensure_trace_exists(
                connection=connection,
                trace_id=query.trace_id,
                operation=LogicTraceOperation.GET_TRACE,
                request_id=query.request_id,
            )
            events = []
            if query.include_events:
                events = [
                    _row_to_event_dto(row)
                    for row in (
                        connection.execute(
                            select(LOGIC_TRACE_EVENT_TABLE)
                            .where(LOGIC_TRACE_EVENT_TABLE.c.trace_id == query.trace_id)
                            .order_by(LOGIC_TRACE_EVENT_TABLE.c.created_at.asc())
                        )
                        .mappings()
                        .all()
                    )
                ]
            call_summaries = []
            if query.include_calls:
                call_summaries = [
                    _row_to_call_summary_dto(row)
                    for row in (
                        connection.execute(
                            select(LOGIC_TRACE_CALL_SUMMARY_TABLE)
                            .where(
                                LOGIC_TRACE_CALL_SUMMARY_TABLE.c.trace_id
                                == query.trace_id
                            )
                            .order_by(LOGIC_TRACE_CALL_SUMMARY_TABLE.c.created_at.asc())
                        )
                        .mappings()
                        .all()
                    )
                ]
            artifacts = []
            if query.include_artifacts:
                artifacts = [
                    _row_to_artifact_dto(row)
                    for row in (
                        connection.execute(
                            select(LOGIC_TRACE_ARTIFACT_TABLE)
                            .where(
                                LOGIC_TRACE_ARTIFACT_TABLE.c.trace_id == query.trace_id
                            )
                            .order_by(LOGIC_TRACE_ARTIFACT_TABLE.c.created_at.asc())
                        )
                        .mappings()
                        .all()
                    )
                ]
            projections = []
            if query.include_projections:
                projections = [
                    _row_to_projection_dto(row)
                    for row in (
                        connection.execute(
                            select(LOGIC_TRACE_PROJECTION_TABLE)
                            .where(
                                LOGIC_TRACE_PROJECTION_TABLE.c.trace_id
                                == query.trace_id
                            )
                            .order_by(LOGIC_TRACE_PROJECTION_TABLE.c.updated_at.asc())
                        )
                        .mappings()
                        .all()
                    )
                ]
            return TraceDetailDto(
                trace=trace,
                events=events,
                call_summaries=call_summaries,
                artifacts=artifacts,
                projections=projections,
            )

    def _list_traces_sync(
        self,
        query: ListTracesQueryDto,
    ) -> LogicTraceQueryResultDto:
        """同步分页读取逻辑链列表。

        :param query: 分页查询逻辑链的查询 DTO。
        :return: 逻辑链分页查询结果。
        """

        with self._engine.begin() as connection:
            statement = select(LOGIC_TRACE_TABLE)
            if query.session_id is not None:
                statement = statement.where(
                    LOGIC_TRACE_TABLE.c.session_id == query.session_id
                )
            if query.run_id is not None:
                statement = statement.where(LOGIC_TRACE_TABLE.c.run_id == query.run_id)
            if query.request_id is not None:
                statement = statement.where(
                    LOGIC_TRACE_TABLE.c.request_id == query.request_id
                )
            if query.trace_ids:
                statement = statement.where(
                    LOGIC_TRACE_TABLE.c.trace_id.in_(query.trace_ids)
                )
            count_statement = select(func.count()).select_from(statement.subquery())
            total = int(connection.execute(count_statement).scalar_one())
            rows = (
                connection.execute(
                    statement.order_by(LOGIC_TRACE_TABLE.c.started_at.desc())
                    .offset(query.offset)
                    .limit(query.limit)
                )
                .mappings()
                .all()
            )
            return LogicTraceQueryResultDto(
                traces=[_row_to_trace_dto(row) for row in rows],
                total=total,
            )

    async def start_logic_trace(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """启动一轮通用逻辑链。

        :param command: 启动逻辑链的命令 DTO。
        :return: 通用逻辑链启动写入结果。
        """

        return await self._execute_write_operation(
            operation=LogicTraceOperation.START_TRACE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._start_trace_sync, command),
        )

    async def start_trace(
        self,
        command: StartTraceCommandDto | AgentTraceStartCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """启动一轮逻辑链。

        :param command: 启动逻辑链的命令 DTO。
        :return: 启动写入结果；若输入为 Agent 语义 DTO，则返回 Agent 语义写入结果。
        """

        if isinstance(command, AgentTraceStartCommandDto):
            result = await self.start_logic_trace(
                StartTraceCommandDto(
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    turn_id=command.turn_id,
                    run_id=command.run_id,
                    session_id=command.session_id,
                    user_id=command.user_id,
                    pet_id=command.pet_id,
                    params_version=command.params_version,
                    config_snapshot_id=command.config_snapshot_id,
                    idempotency_key=command.idempotency_key,
                )
            )
            return AgentTraceWriteResultDto(
                status=_map_write_status_for_agent(result.status),
                error_code=result.error_code,
                retryable=result.retryable,
                detail=result.detail,
            )
        return await self.start_logic_trace(command)

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """追加一条逻辑链事件。

        :param command: 追加逻辑链事件的命令 DTO。
        :return: 逻辑链事件写入结果。
        """

        schema_result = await self._schema_validator.validate_trace_event(command)
        if not schema_result.valid:
            error = _build_logic_trace_store_error(
                code=LogicTraceErrorCode.TRACE_EVENT_SCHEMA_INVALID,
                operation=LogicTraceOperation.APPEND_TRACE_EVENT,
                message="logic trace event schema 校验失败",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={
                    "errors": list(schema_result.errors),
                    "warnings": list(schema_result.warnings),
                },
            )
            return _logic_error_to_write_result(error)
        return await self._execute_write_operation(
            operation=LogicTraceOperation.APPEND_TRACE_EVENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._append_trace_event_sync,
                command,
                schema_result,
            ),
        )

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一次调用摘要。

        :param command: 记录调用摘要的命令 DTO。
        :return: 调用摘要写入结果。
        """

        return await self._execute_write_operation(
            operation=LogicTraceOperation.RECORD_CALL_SUMMARY,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._record_call_summary_sync, command),
        )

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录一个 trace artifact。

        :param command: 记录 trace artifact 的命令 DTO。
        :return: artifact 写入结果。
        """

        return await self._execute_write_operation(
            operation=LogicTraceOperation.RECORD_TRACE_ARTIFACT,
            request_id=None,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._record_trace_artifact_sync, command),
        )

    async def finalize_logic_trace(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """完成一轮通用逻辑链。

        :param command: 完成逻辑链的命令 DTO。
        :return: 通用逻辑链完成写入结果。
        """

        return await self._execute_write_operation(
            operation=LogicTraceOperation.FINALIZE_TRACE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._finalize_trace_sync, command),
        )

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto | AgentTraceFinalizeCommandDto,
    ) -> LogicTraceWriteResultDto | AgentTraceWriteResultDto:
        """完成一轮逻辑链。

        :param command: 完成逻辑链的命令 DTO。
        :return: 完成写入结果；若输入为 Agent 语义 DTO，则返回 Agent 语义写入结果。
        """

        if isinstance(command, AgentTraceFinalizeCommandDto):
            result = await self.finalize_logic_trace(
                FinalizeTraceCommandDto(
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    turn_id=command.turn_id,
                    run_id=command.run_id,
                    final_status=LogicTraceFinalStatus(command.final_status.value),
                    user_message_id=command.user_message_id,
                    error_code=command.error_code,
                    summary=dict(command.summary),
                    finalized_at=_now_utc(),
                )
            )
            return AgentTraceWriteResultDto(
                status=_map_write_status_for_agent(result.status),
                error_code=result.error_code,
                retryable=result.retryable,
                detail=result.detail,
            )
        return await self.finalize_logic_trace(command)

    async def build_trace_projection(
        self,
        command: BuildTraceProjectionCommandDto,
    ) -> TraceProjectionDto:
        """构建并持久化逻辑链投影。

        :param command: 构建逻辑链投影的命令 DTO。
        :return: 已写入或更新的逻辑链投影 DTO。
        """

        return await self._run_with_timeout(
            operation=LogicTraceOperation.BUILD_TRACE_PROJECTION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._build_projection_sync, command),
        )

    async def get_trace(
        self,
        query: GetTraceQueryDto,
    ) -> TraceDetailDto:
        """查询单条逻辑链详情。

        :param query: 查询单条逻辑链的查询 DTO。
        :return: 逻辑链详情 DTO。
        """

        return await self._run_with_timeout(
            operation=LogicTraceOperation.GET_TRACE,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(self._get_trace_sync, query),
        )

    async def list_traces(
        self,
        query: ListTracesQueryDto,
    ) -> LogicTraceQueryResultDto:
        """分页查询逻辑链列表。

        :param query: 分页查询逻辑链的查询 DTO。
        :return: 逻辑链分页查询结果。
        """

        return await self._run_with_timeout(
            operation=LogicTraceOperation.LIST_TRACES,
            request_id=query.request_id,
            trace_id=None,
            awaitable=asyncio.to_thread(self._list_traces_sync, query),
        )

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """记录一次模型调用摘要。

        :param summary: 脱敏模型调用摘要。
        :return: 模型调用摘要写入结果。
        """

        result = await self.record_call_summary(
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
                created_at=_now_utc(),
            )
        )
        return LlmTraceWriteResultDto(
            status=_map_write_status_for_llm(result.status),
            reason=result.detail,
        )

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """记录一次 AgentRunner 运行摘要。

        :param summary: AgentRunner 脱敏运行摘要。
        :return: AgentRunner 运行摘要写入结果。
        """

        result = await self.record_call_summary(
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
                created_at=_now_utc(),
            )
        )
        return AgentRunnerTraceWriteResultDto(
            status=_map_write_status_for_agent_runner(result.status),
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """记录一次宠物会话策略判定摘要。

        :param record: 宠物会话策略判定摘要。
        :return: 宠物会话策略摘要写入结果。
        """

        result = await self.record_call_summary(
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
                created_at=_now_utc(),
            )
        )
        return PetSessionTraceWriteResultDto(
            status=_map_write_status_for_pet_session(result.status),
            error_code=result.error_code,
            retryable=result.retryable,
            detail=result.detail,
        )


def create_sqlalchemy_logic_trace_store(
    database_url: str,
    *,
    settings: LogicTraceStoreSettings | None = None,
    schema_validator: LogicTraceSchemaValidator | None = None,
) -> SqlAlchemyLogicTraceStore:
    """创建 SQLAlchemy LogicTraceStore。

    :param database_url: SQLAlchemy 数据库连接字符串。
    :param settings: 可选 LogicTraceStore 运行配置。
    :param schema_validator: 可选逻辑链事件 schema 校验器。
    :return: 已装配好的 SQLAlchemy LogicTraceStore 实例。
    """

    return SqlAlchemyLogicTraceStore(
        engine=create_engine(database_url),
        settings=settings,
        schema_validator=schema_validator,
    )


__all__: tuple[str, ...] = (
    "LOGIC_TRACE_ARTIFACT_TABLE",
    "LOGIC_TRACE_CALL_SUMMARY_TABLE",
    "LOGIC_TRACE_EVENT_TABLE",
    "LOGIC_TRACE_OUTBOX_TABLE",
    "LOGIC_TRACE_PROJECTION_TABLE",
    "LOGIC_TRACE_STORE_METADATA",
    "LOGIC_TRACE_TABLE",
    "SqlAlchemyLogicTraceStore",
    "create_sqlalchemy_logic_trace_store",
)
