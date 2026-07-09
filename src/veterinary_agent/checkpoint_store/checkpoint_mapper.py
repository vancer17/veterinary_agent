##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/checkpoint_mapper.py
# 作用: 将 LangGraph CheckpointTuple 映射为 CheckpointStore 公共 DTO，并集中执行项目级
#       metadata envelope、schema version 与状态体结构校验。
# 边界: 仅做结构转换与领域错误映射；不访问数据库、不调用 LangGraph 读取 API、不解释兽医业务语义。
##################################################################################################

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from langgraph.checkpoint.base import CheckpointTuple
from pydantic import ValidationError

from veterinary_agent.checkpoint_store.dto import (
    CheckpointSnapshotDto,
    CheckpointSummaryDto,
    GraphExecutionStateDto,
    JsonMap,
    SegmentPublishStateDto,
    SessionBusinessStateDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
    SegmentPublishStatus,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError

CHECKPOINT_METADATA_ENVELOPE_KEY: Final[str] = "checkpoint_store"
CHECKPOINT_METADATA_MANAGED_FLAG: Final[str] = "checkpoint_store_managed"
CHECKPOINT_GRAPH_STATE_CHANNEL: Final[str] = "graph_state"
CHECKPOINT_BUSINESS_STATE_CHANNEL: Final[str] = "business_state"


def _build_mapping_error(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str,
    trace_id: str,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 checkpoint 映射阶段领域错误。

    :param code: CheckpointStore 稳定错误码。
    :param operation: 当前 CheckpointStore 操作名。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param conflict_with: 可选冲突对象摘要。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=code,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=False,
        conflict_with=conflict_with,
    )


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将对象安全识别为字符串键映射。

    :param value: 待识别的对象。
    :return: 若对象是映射则返回字符串键映射；否则返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _copy_json_map(value: object) -> JsonMap:
    """将对象复制为 CheckpointStore JSON map。

    :param value: 待复制的对象。
    :return: 字符串键 JSON map；非映射对象返回空字典。
    """

    mapping = _as_mapping(value)
    if mapping is None:
        return {}
    return dict(mapping)


def _get_configurable(checkpoint_tuple: CheckpointTuple) -> Mapping[str, object]:
    """读取 LangGraph checkpoint tuple 的 configurable 配置。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :return: configurable 配置映射。
    :raises KeyError: 当 tuple config 缺少 configurable 时抛出。
    """

    config_mapping = _as_mapping(checkpoint_tuple.config)
    if config_mapping is None:
        raise KeyError("config")
    configurable = _as_mapping(config_mapping.get("configurable"))
    if configurable is None:
        raise KeyError("configurable")
    return configurable


def _get_checkpoint_id(checkpoint_tuple: CheckpointTuple) -> str:
    """读取 LangGraph checkpoint ID。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :return: checkpoint ID。
    :raises KeyError: 当 checkpoint ID 缺失时抛出。
    """

    configurable = _get_configurable(checkpoint_tuple)
    checkpoint_id = configurable.get("checkpoint_id")
    if isinstance(checkpoint_id, str) and checkpoint_id.strip():
        return checkpoint_id
    checkpoint_map = _as_mapping(checkpoint_tuple.checkpoint)
    if checkpoint_map is not None:
        checkpoint_id = checkpoint_map.get("id")
        if isinstance(checkpoint_id, str) and checkpoint_id.strip():
            return checkpoint_id
    raise KeyError("checkpoint_id")


def _get_thread_id(checkpoint_tuple: CheckpointTuple) -> str:
    """读取 LangGraph checkpoint 所属 thread ID。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :return: thread ID。
    :raises KeyError: 当 thread ID 缺失时抛出。
    """

    configurable = _get_configurable(checkpoint_tuple)
    thread_id = configurable.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id
    raise KeyError("thread_id")


def _get_metadata(checkpoint_tuple: CheckpointTuple) -> Mapping[str, object]:
    """读取 LangGraph checkpoint metadata。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :return: metadata 映射。
    """

    metadata = _as_mapping(checkpoint_tuple.metadata)
    return {} if metadata is None else metadata


def _get_envelope(metadata: Mapping[str, object]) -> Mapping[str, object]:
    """读取项目级 checkpoint metadata envelope。

    :param metadata: LangGraph checkpoint metadata。
    :return: 项目级 metadata envelope；不存在时返回空映射。
    """

    envelope = _as_mapping(metadata.get(CHECKPOINT_METADATA_ENVELOPE_KEY))
    return {} if envelope is None else envelope


def _is_project_managed(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
) -> bool:
    """判断 checkpoint 是否声明由 CheckpointStore 项目契约管理。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :return: 若 checkpoint 声明为项目托管则返回 True。
    """

    return metadata.get(CHECKPOINT_METADATA_MANAGED_FLAG) is True or envelope.get(
        CHECKPOINT_METADATA_MANAGED_FLAG
    ) is True


def _read_field(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
    field_name: str,
) -> object:
    """从 envelope 与 metadata 中读取项目字段。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :param field_name: 需要读取的字段名。
    :return: 字段值；缺失时返回 None。
    """

    if field_name in envelope:
        return envelope[field_name]
    return metadata.get(field_name)


def _read_required_str(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
    field_name: str,
) -> str:
    """读取必填字符串项目字段。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :param field_name: 需要读取的字段名。
    :return: 非空字符串字段值。
    :raises KeyError: 当字段缺失或不是非空字符串时抛出。
    """

    value = _read_field(metadata=metadata, envelope=envelope, field_name=field_name)
    if isinstance(value, str) and value.strip():
        return value
    raise KeyError(field_name)


def _read_optional_str(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
    field_name: str,
) -> str | None:
    """读取可选字符串项目字段。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :param field_name: 需要读取的字段名。
    :return: 非空字符串字段值；缺失时返回 None。
    """

    value = _read_field(metadata=metadata, envelope=envelope, field_name=field_name)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _read_required_int(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
    field_name: str,
) -> int:
    """读取必填整数项目字段。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :param field_name: 需要读取的字段名。
    :return: 整数字段值。
    :raises KeyError: 当字段缺失或不是整数时抛出。
    """

    value = _read_field(metadata=metadata, envelope=envelope, field_name=field_name)
    if isinstance(value, bool):
        raise KeyError(field_name)
    if isinstance(value, int):
        return value
    raise KeyError(field_name)


def _read_status(
    *,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
) -> CheckpointRecordStatus:
    """读取 checkpoint 快照状态。

    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :return: checkpoint 快照状态枚举。
    :raises ValueError: 当状态值不属于 CheckpointRecordStatus 时抛出。
    """

    return CheckpointRecordStatus(
        _read_required_str(
            metadata=metadata,
            envelope=envelope,
            field_name="status",
        )
    )


def _parse_created_at(value: object) -> datetime:
    """解析 checkpoint 创建时间。

    :param value: 待解析的时间字段。
    :return: UTC aware datetime。
    :raises ValueError: 当时间字段无法解析时抛出。
    """

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str) and value.strip():
        normalized_value = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized_value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    raise ValueError("created_at")


def _read_created_at(
    *,
    checkpoint_tuple: CheckpointTuple,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
) -> datetime:
    """读取 checkpoint 创建时间。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :return: checkpoint 创建时间。
    :raises ValueError: 当创建时间缺失或无法解析时抛出。
    """

    created_at = _read_field(
        metadata=metadata,
        envelope=envelope,
        field_name="created_at",
    )
    if created_at is not None:
        return _parse_created_at(created_at)
    checkpoint_map = _as_mapping(checkpoint_tuple.checkpoint)
    if checkpoint_map is not None:
        return _parse_created_at(checkpoint_map.get("ts"))
    raise ValueError("created_at")


def _get_channel_values(checkpoint_tuple: CheckpointTuple) -> Mapping[str, object]:
    """读取 LangGraph checkpoint channel_values。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :return: channel_values 映射。
    """

    checkpoint_map = _as_mapping(checkpoint_tuple.checkpoint)
    if checkpoint_map is None:
        return {}
    channel_values = _as_mapping(checkpoint_map.get("channel_values"))
    return {} if channel_values is None else channel_values


def _read_state_source(
    *,
    checkpoint_tuple: CheckpointTuple,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
    channel_name: str,
) -> Mapping[str, object]:
    """读取 checkpoint 中指定状态源。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :param channel_name: 需要读取的状态 channel 名。
    :return: 状态源映射。
    :raises KeyError: 当状态源缺失或不是映射时抛出。
    """

    channel_values = _get_channel_values(checkpoint_tuple)
    source = _as_mapping(channel_values.get(channel_name))
    if source is not None:
        return source
    source = _as_mapping(_read_field(metadata=metadata, envelope=envelope, field_name=channel_name))
    if source is not None:
        return source
    raise KeyError(channel_name)


def _build_graph_state(
    *,
    checkpoint_tuple: CheckpointTuple,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
) -> GraphExecutionStateDto:
    """构建图执行恢复状态 DTO。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :return: 图执行恢复状态 DTO。
    :raises ValidationError: 当状态源不符合 DTO 契约时抛出。
    """

    return GraphExecutionStateDto.model_validate(
        _read_state_source(
            checkpoint_tuple=checkpoint_tuple,
            metadata=metadata,
            envelope=envelope,
            channel_name=CHECKPOINT_GRAPH_STATE_CHANNEL,
        )
    )


def _build_business_state(
    *,
    checkpoint_tuple: CheckpointTuple,
    metadata: Mapping[str, object],
    envelope: Mapping[str, object],
) -> SessionBusinessStateDto:
    """构建 session 短期业务状态 DTO。

    :param checkpoint_tuple: LangGraph checkpoint tuple。
    :param metadata: LangGraph checkpoint metadata。
    :param envelope: 项目级 metadata envelope。
    :return: session 短期业务状态 DTO。
    :raises ValidationError: 当状态源不符合 DTO 契约时抛出。
    """

    return SessionBusinessStateDto.model_validate(
        _read_state_source(
            checkpoint_tuple=checkpoint_tuple,
            metadata=metadata,
            envelope=envelope,
            channel_name=CHECKPOINT_BUSINESS_STATE_CHANNEL,
        )
    )


def _filter_published_segments(
    segments: list[SegmentPublishStateDto],
) -> list[SegmentPublishStateDto]:
    """过滤已发布 segment 状态。

    :param segments: checkpoint 中保存的 segment 状态列表。
    :return: 状态为 published 的 segment 状态列表。
    """

    return [
        segment
        for segment in segments
        if segment.status is SegmentPublishStatus.PUBLISHED
    ]


class CheckpointTupleMapper:
    """LangGraph checkpoint tuple 到 CheckpointStore DTO 的映射器。"""

    def __init__(
        self,
        *,
        supported_state_schema_versions: frozenset[str] | None = None,
    ) -> None:
        """初始化 checkpoint tuple 映射器。

        :param supported_state_schema_versions: 可选支持的状态 schema 版本集合；为空表示仅校验非空。
        :return: None。
        """

        self._supported_state_schema_versions = supported_state_schema_versions

    def to_snapshot(
        self,
        *,
        checkpoint_tuple: CheckpointTuple,
        expected_thread_id: str,
        expected_checkpoint_id: str | None,
        expected_version: int | None,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> CheckpointSnapshotDto:
        """将 LangGraph checkpoint tuple 映射为完整 checkpoint 快照 DTO。

        :param checkpoint_tuple: LangGraph checkpoint tuple。
        :param expected_thread_id: 调用方期望的 thread ID。
        :param expected_checkpoint_id: 可选调用方期望的 checkpoint ID。
        :param expected_version: 可选调用方期望的项目版本号。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: checkpoint 快照 DTO。
        :raises CheckpointStoreError: 当 tuple 结构损坏、归属不一致或 schema 不支持时抛出。
        """

        try:
            metadata = _get_metadata(checkpoint_tuple)
            envelope = _get_envelope(metadata)
            self._ensure_project_managed(
                metadata=metadata,
                envelope=envelope,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            thread_id = _get_thread_id(checkpoint_tuple)
            checkpoint_id = _get_checkpoint_id(checkpoint_tuple)
            self._ensure_identity_matches(
                actual_thread_id=thread_id,
                expected_thread_id=expected_thread_id,
                actual_checkpoint_id=checkpoint_id,
                expected_checkpoint_id=expected_checkpoint_id,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            version = _read_required_int(
                metadata=metadata,
                envelope=envelope,
                field_name="version",
            )
            self._ensure_version_matches(
                actual_version=version,
                expected_version=expected_version,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            state_schema_version = _read_required_str(
                metadata=metadata,
                envelope=envelope,
                field_name="state_schema_version",
            )
            self._ensure_schema_supported(
                state_schema_version=state_schema_version,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            graph_state = _build_graph_state(
                checkpoint_tuple=checkpoint_tuple,
                metadata=metadata,
                envelope=envelope,
            )
            business_state = _build_business_state(
                checkpoint_tuple=checkpoint_tuple,
                metadata=metadata,
                envelope=envelope,
            )
            snapshot_metadata = _copy_json_map(
                _read_field(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="metadata",
                )
            )
            return CheckpointSnapshotDto(
                checkpoint_id=checkpoint_id,
                thread_id=thread_id,
                run_id=_read_required_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="run_id",
                ),
                version=version,
                graph_name=_read_required_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="graph_name",
                ),
                graph_version=_read_required_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="graph_version",
                ),
                state_schema_version=state_schema_version,
                status=_read_status(metadata=metadata, envelope=envelope),
                current_node=_read_optional_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="current_node",
                ),
                graph_state=graph_state,
                business_state=business_state,
                metadata=snapshot_metadata,
                published_segments=_filter_published_segments(
                    business_state.segments
                ),
                state_size_bytes=_read_required_int(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="state_size_bytes",
                ),
                created_at=_read_created_at(
                    checkpoint_tuple=checkpoint_tuple,
                    metadata=metadata,
                    envelope=envelope,
                ),
            )
        except CheckpointStoreError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise _build_mapping_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="LangGraph checkpoint 无法映射为 CheckpointSnapshotDto",
                request_id=request_id,
                trace_id=trace_id,
                conflict_with={"reason": str(exc)},
            ) from exc

    def to_summary(
        self,
        *,
        checkpoint_tuple: CheckpointTuple,
        expected_thread_id: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> CheckpointSummaryDto:
        """将 LangGraph checkpoint tuple 映射为 checkpoint 历史摘要 DTO。

        :param checkpoint_tuple: LangGraph checkpoint tuple。
        :param expected_thread_id: 调用方期望的 thread ID。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: checkpoint 历史摘要 DTO。
        :raises CheckpointStoreError: 当 tuple 结构损坏、归属不一致或 schema 不支持时抛出。
        """

        try:
            metadata = _get_metadata(checkpoint_tuple)
            envelope = _get_envelope(metadata)
            self._ensure_project_managed(
                metadata=metadata,
                envelope=envelope,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            thread_id = _get_thread_id(checkpoint_tuple)
            checkpoint_id = _get_checkpoint_id(checkpoint_tuple)
            self._ensure_identity_matches(
                actual_thread_id=thread_id,
                expected_thread_id=expected_thread_id,
                actual_checkpoint_id=checkpoint_id,
                expected_checkpoint_id=None,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            state_schema_version = _read_required_str(
                metadata=metadata,
                envelope=envelope,
                field_name="state_schema_version",
            )
            self._ensure_schema_supported(
                state_schema_version=state_schema_version,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
            )
            return CheckpointSummaryDto(
                checkpoint_id=checkpoint_id,
                version=_read_required_int(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="version",
                ),
                status=_read_status(metadata=metadata, envelope=envelope),
                current_node=_read_optional_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="current_node",
                ),
                graph_version=_read_required_str(
                    metadata=metadata,
                    envelope=envelope,
                    field_name="graph_version",
                ),
                state_schema_version=state_schema_version,
                created_at=_read_created_at(
                    checkpoint_tuple=checkpoint_tuple,
                    metadata=metadata,
                    envelope=envelope,
                ),
            )
        except CheckpointStoreError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise _build_mapping_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="LangGraph checkpoint 无法映射为 CheckpointSummaryDto",
                request_id=request_id,
                trace_id=trace_id,
                conflict_with={"reason": str(exc)},
            ) from exc

    def _ensure_project_managed(
        self,
        *,
        metadata: Mapping[str, object],
        envelope: Mapping[str, object],
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> None:
        """确认 checkpoint 由项目级 CheckpointStore 契约管理。

        :param metadata: LangGraph checkpoint metadata。
        :param envelope: 项目级 metadata envelope。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: None。
        :raises CheckpointStoreError: 当 checkpoint 未声明项目托管时抛出。
        """

        if _is_project_managed(metadata=metadata, envelope=envelope):
            return
        raise _build_mapping_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="LangGraph checkpoint 缺少 CheckpointStore 项目 envelope",
            request_id=request_id,
            trace_id=trace_id,
        )

    def _ensure_identity_matches(
        self,
        *,
        actual_thread_id: str,
        expected_thread_id: str,
        actual_checkpoint_id: str,
        expected_checkpoint_id: str | None,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> None:
        """确认 checkpoint 归属与查询条件一致。

        :param actual_thread_id: checkpoint tuple 中的实际 thread ID。
        :param expected_thread_id: 调用方期望的 thread ID。
        :param actual_checkpoint_id: checkpoint tuple 中的实际 checkpoint ID。
        :param expected_checkpoint_id: 调用方期望的 checkpoint ID；为空则不校验。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: None。
        :raises CheckpointStoreError: 当归属不一致时抛出。
        """

        if actual_thread_id != expected_thread_id:
            raise _build_mapping_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="LangGraph checkpoint thread_id 与查询条件不一致",
                request_id=request_id,
                trace_id=trace_id,
                conflict_with={
                    "expected_thread_id": expected_thread_id,
                    "actual_thread_id": actual_thread_id,
                },
            )
        if expected_checkpoint_id is None or actual_checkpoint_id == expected_checkpoint_id:
            return
        raise _build_mapping_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="LangGraph checkpoint_id 与查询条件不一致",
            request_id=request_id,
            trace_id=trace_id,
            conflict_with={
                "expected_checkpoint_id": expected_checkpoint_id,
                "actual_checkpoint_id": actual_checkpoint_id,
            },
        )

    def _ensure_version_matches(
        self,
        *,
        actual_version: int,
        expected_version: int | None,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> None:
        """确认 checkpoint 项目版本与控制面版本一致。

        :param actual_version: checkpoint metadata 中的实际项目版本。
        :param expected_version: 控制面期望的项目版本；为空则不校验。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: None。
        :raises CheckpointStoreError: 当版本不一致时抛出。
        """

        if expected_version is None or actual_version == expected_version:
            return
        raise _build_mapping_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="LangGraph checkpoint 项目版本与控制面 latest_version 不一致",
            request_id=request_id,
            trace_id=trace_id,
            conflict_with={
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        )

    def _ensure_schema_supported(
        self,
        *,
        state_schema_version: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> None:
        """确认 checkpoint 状态 schema 版本受当前代码支持。

        :param state_schema_version: checkpoint 状态 schema 版本。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: None。
        :raises CheckpointStoreError: 当 schema 版本不受支持时抛出。
        """

        if self._supported_state_schema_versions is None:
            return
        if state_schema_version in self._supported_state_schema_versions:
            return
        raise _build_mapping_error(
            code=CheckpointErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED,
            operation=operation,
            message="checkpoint 状态 schema 版本不受支持",
            request_id=request_id,
            trace_id=trace_id,
            conflict_with={"state_schema_version": state_schema_version},
        )


__all__: tuple[str, ...] = (
    "CHECKPOINT_BUSINESS_STATE_CHANNEL",
    "CHECKPOINT_GRAPH_STATE_CHANNEL",
    "CHECKPOINT_METADATA_ENVELOPE_KEY",
    "CHECKPOINT_METADATA_MANAGED_FLAG",
    "CheckpointTupleMapper",
)
