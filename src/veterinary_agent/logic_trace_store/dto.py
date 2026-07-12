##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/dto.py
# 作用: 定义 LogicTraceStore 的公共 DTO、查询对象、投影对象与写入结果。
# 边界: 仅承载通用逻辑链留痕数据结构，不实现数据库访问、VetTraceSchema 语义校验或业务编排。
##################################################################################################

from datetime import datetime
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.logic_trace_store.enums import (
    LogicTraceFinalStatus,
    LogicTraceStatus,
    LogicTraceWriteStatus,
    TraceArtifactType,
    TraceCallStatus,
    TraceCallType,
    TraceOutboxStatus,
    TraceProjectionType,
)

JsonMap: TypeAlias = dict[str, object]


class LogicTraceStoreDto(BaseModel):
    """LogicTraceStore DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class LogicTraceStoreSettings(LogicTraceStoreDto):
    """LogicTraceStore 运行配置。"""

    operation_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="单次 LogicTraceStore 对外操作允许的最大耗时，单位为秒。",
    )
    max_event_payload_bytes: int = Field(
        default=65_536,
        ge=1,
        description="单个 trace event 允许的最大 JSON 字节数。",
    )
    max_call_summary_bytes: int = Field(
        default=32_768,
        ge=1,
        description="单个 call summary 允许的最大 JSON 字节数。",
    )
    max_artifact_ref_bytes: int = Field(
        default=8_192,
        ge=1,
        description="artifact 引用允许的最大 JSON 字节数。",
    )
    max_projection_bytes: int = Field(
        default=65_536,
        ge=1,
        description="单个 projection 允许的最大 JSON 字节数。",
    )
    max_trace_events: int = Field(
        default=500,
        ge=1,
        description="单条 trace 允许保存的最大事件数。",
    )
    max_outbox_backlog: int = Field(
        default=10_000,
        ge=0,
        description="outbox backlog 软阈值。",
    )
    projection_version: str = Field(
        default="logic-trace.projection.v1",
        min_length=1,
        description="当前投影版本标识。",
    )


class StartTraceCommandDto(LogicTraceStoreDto):
    """启动一轮逻辑链的命令 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    session_id: str = Field(min_length=1, description="当前 session ID。")
    user_id: str = Field(min_length=1, description="当前用户 ID。")
    pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    params_version: str = Field(min_length=1, description="本轮业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="本轮配置快照 ID。")
    idempotency_key: str = Field(min_length=1, description="整轮幂等键。")
    capture_policy_ref: str | None = Field(
        default=None,
        min_length=1,
        description="本轮捕获策略引用。",
    )


class AppendTraceEventCommandDto(LogicTraceStoreDto):
    """追加逻辑链事件的命令 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    event_id: str = Field(min_length=1, description="稳定事件 ID。")
    event_type: str = Field(min_length=1, description="逻辑链事件类型。")
    source_component: str = Field(min_length=1, description="事件来源组件。")
    created_at: datetime = Field(description="事件创建时间。")
    node_id: str | None = Field(default=None, min_length=1, description="节点 ID。")
    task_id: str | None = Field(default=None, min_length=1, description="任务 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="业务分段 ID。",
    )
    input_hash: str | None = Field(
        default=None,
        min_length=1,
        description="输入摘要 hash。",
    )
    output_hash: str | None = Field(
        default=None,
        min_length=1,
        description="输出摘要 hash。",
    )
    summary: JsonMap = Field(default_factory=dict, description="通用事件摘要。")
    business_payload: JsonMap = Field(
        default_factory=dict,
        description="业务扩展字段。",
    )
    schema_ref: str | None = Field(
        default=None,
        min_length=1,
        description="业务 schema 或 capture policy 引用。",
    )


class RecordCallSummaryCommandDto(LogicTraceStoreDto):
    """记录调用摘要的命令 DTO。"""

    call_id: str = Field(min_length=1, description="稳定调用 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    request_id: str = Field(min_length=1, description="本次请求 ID。")
    call_type: TraceCallType = Field(description="调用摘要类型。")
    source_component: str = Field(min_length=1, description="调用来源组件。")
    provider_ref: str | None = Field(
        default=None,
        min_length=1,
        description="上游提供方引用。",
    )
    input_ref: str | None = Field(default=None, min_length=1, description="输入引用。")
    output_ref: str | None = Field(default=None, min_length=1, description="输出引用。")
    usage: JsonMap = Field(default_factory=dict, description="资源使用摘要。")
    status: TraceCallStatus = Field(description="调用最终状态。")
    summary: JsonMap = Field(default_factory=dict, description="调用摘要扩展字段。")
    created_at: datetime = Field(description="调用摘要创建时间。")


class RecordTraceArtifactCommandDto(LogicTraceStoreDto):
    """记录 trace artifact 的命令 DTO。"""

    artifact_id: str = Field(min_length=1, description="稳定 artifact ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    artifact_type: TraceArtifactType = Field(description="artifact 类型。")
    storage_ref: str = Field(min_length=1, description="外部存储引用。")
    content_hash: str | None = Field(
        default=None,
        min_length=1,
        description="artifact 内容 hash。",
    )
    visibility_policy: str = Field(
        min_length=1,
        description="artifact 可见性策略。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="artifact 元信息。")
    created_at: datetime = Field(description="artifact 创建时间。")


class FinalizeTraceCommandDto(LogicTraceStoreDto):
    """完成逻辑链的命令 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    final_status: LogicTraceFinalStatus = Field(description="逻辑链最终状态。")
    user_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="已保存的用户消息 ID。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="失败时的稳定错误码。",
    )
    summary: JsonMap = Field(default_factory=dict, description="最终摘要。")
    finalized_at: datetime = Field(description="最终完成时间。")


class BuildTraceProjectionCommandDto(LogicTraceStoreDto):
    """构建逻辑链投影的命令 DTO。"""

    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    projection_type: TraceProjectionType = Field(description="投影类型。")
    version: str = Field(min_length=1, description="投影版本。")
    request_id: str | None = Field(default=None, min_length=1, description="请求 ID。")
    segment_id: str | None = Field(
        default=None, min_length=1, description="关联 segment ID。"
    )
    event_id: str | None = Field(
        default=None, min_length=1, description="关联事件 ID。"
    )
    display_payload: JsonMap = Field(
        default_factory=dict,
        description="调用方提供的展示负载。",
    )


class GetTraceQueryDto(LogicTraceStoreDto):
    """查询单条逻辑链的查询 DTO。"""

    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    request_id: str | None = Field(default=None, min_length=1, description="请求 ID。")
    include_events: bool = Field(default=True, description="是否返回原始事件。")
    include_calls: bool = Field(default=True, description="是否返回调用摘要。")
    include_artifacts: bool = Field(default=True, description="是否返回 artifact。")
    include_projections: bool = Field(default=True, description="是否返回投影。")


class ListTracesQueryDto(LogicTraceStoreDto):
    """分页查询逻辑链的查询 DTO。"""

    session_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选 session 过滤条件。",
    )
    run_id: str | None = Field(
        default=None, min_length=1, description="可选 run 过滤条件。"
    )
    request_id: str | None = Field(
        default=None, min_length=1, description="可选 request 过滤条件。"
    )
    trace_ids: list[str] = Field(
        default_factory=list, description="可选 trace ID 过滤列表。"
    )
    limit: int = Field(default=50, ge=1, le=500, description="单次返回条数。")
    offset: int = Field(default=0, ge=0, description="分页偏移量。")


class TraceProjectionDto(LogicTraceStoreDto):
    """逻辑链投影 DTO。"""

    projection_id: str = Field(min_length=1, description="投影 ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    projection_type: TraceProjectionType = Field(description="投影类型。")
    version: str = Field(min_length=1, description="投影版本。")
    view_payload: JsonMap = Field(default_factory=dict, description="投影负载。")
    created_at: datetime | None = Field(default=None, description="创建时间。")
    updated_at: datetime = Field(description="最后更新时间。")


class TraceEventDto(LogicTraceStoreDto):
    """逻辑链原始事件 DTO。"""

    event_id: str = Field(min_length=1, description="事件 ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    request_id: str = Field(min_length=1, description="请求 ID。")
    event_type: str = Field(min_length=1, description="事件类型。")
    source_component: str = Field(min_length=1, description="来源组件。")
    created_at: datetime = Field(description="事件创建时间。")
    node_id: str | None = Field(default=None, min_length=1, description="节点 ID。")
    task_id: str | None = Field(default=None, min_length=1, description="任务 ID。")
    segment_id: str | None = Field(default=None, min_length=1, description="分段 ID。")
    input_hash: str | None = Field(
        default=None, min_length=1, description="输入 hash。"
    )
    output_hash: str | None = Field(
        default=None, min_length=1, description="输出 hash。"
    )
    summary: JsonMap = Field(default_factory=dict, description="事件摘要。")
    business_payload: JsonMap = Field(default_factory=dict, description="业务负载。")
    schema_ref: str | None = Field(
        default=None, min_length=1, description="schema 引用。"
    )


class TraceCallSummaryDto(LogicTraceStoreDto):
    """逻辑链调用摘要 DTO。"""

    call_id: str = Field(min_length=1, description="调用 ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    request_id: str = Field(min_length=1, description="请求 ID。")
    call_type: TraceCallType = Field(description="调用类型。")
    source_component: str = Field(min_length=1, description="来源组件。")
    provider_ref: str | None = Field(
        default=None, min_length=1, description="提供方引用。"
    )
    input_ref: str | None = Field(default=None, min_length=1, description="输入引用。")
    output_ref: str | None = Field(default=None, min_length=1, description="输出引用。")
    usage: JsonMap = Field(default_factory=dict, description="资源使用摘要。")
    status: TraceCallStatus = Field(description="调用状态。")
    summary: JsonMap = Field(default_factory=dict, description="调用摘要。")
    created_at: datetime = Field(description="创建时间。")


class TraceArtifactDto(LogicTraceStoreDto):
    """逻辑链 artifact DTO。"""

    artifact_id: str = Field(min_length=1, description="artifact ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    artifact_type: TraceArtifactType = Field(description="artifact 类型。")
    storage_ref: str = Field(min_length=1, description="存储引用。")
    content_hash: str | None = Field(
        default=None, min_length=1, description="内容 hash。"
    )
    visibility_policy: str = Field(min_length=1, description="可见性策略。")
    metadata: JsonMap = Field(default_factory=dict, description="元信息。")
    created_at: datetime = Field(description="创建时间。")


class TraceOutboxDto(LogicTraceStoreDto):
    """逻辑链 outbox DTO。"""

    outbox_id: str = Field(min_length=1, description="outbox ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    event_kind: str = Field(min_length=1, description="outbox 事件类型。")
    payload: JsonMap = Field(default_factory=dict, description="待补偿负载。")
    status: TraceOutboxStatus = Field(description="outbox 状态。")
    retry_count: int = Field(ge=0, description="重试次数。")
    next_retry_at: datetime | None = Field(
        default=None,
        description="下一次可重试时间。",
    )
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="更新时间。")


class TraceDetailDto(LogicTraceStoreDto):
    """逻辑链详情 DTO。"""

    trace: "TraceDto" = Field(description="逻辑链主体。")
    events: list[TraceEventDto] = Field(default_factory=list, description="事件列表。")
    call_summaries: list[TraceCallSummaryDto] = Field(
        default_factory=list,
        description="调用摘要列表。",
    )
    artifacts: list[TraceArtifactDto] = Field(
        default_factory=list,
        description="artifact 列表。",
    )
    projections: list[TraceProjectionDto] = Field(
        default_factory=list,
        description="投影列表。",
    )


class TraceDto(LogicTraceStoreDto):
    """逻辑链主记录 DTO。"""

    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    request_id: str = Field(min_length=1, description="请求 ID。")
    turn_id: str = Field(min_length=1, description="turn ID。")
    run_id: str = Field(min_length=1, description="运行 ID。")
    session_id: str = Field(min_length=1, description="session ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    pet_id: str = Field(min_length=1, description="宠物 ID。")
    params_version: str = Field(min_length=1, description="参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="配置快照 ID。")
    idempotency_key: str = Field(min_length=1, description="幂等键。")
    capture_policy_ref: str | None = Field(
        default=None,
        min_length=1,
        description="捕获策略引用。",
    )
    status: LogicTraceStatus = Field(description="逻辑链状态。")
    final_status: LogicTraceFinalStatus | None = Field(
        default=None,
        description="逻辑链最终状态。",
    )
    user_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="用户消息 ID。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="失败时的错误码。",
    )
    summary: JsonMap = Field(default_factory=dict, description="逻辑链摘要。")
    started_at: datetime = Field(description="开始时间。")
    finalized_at: datetime | None = Field(
        default=None,
        description="完成时间。",
    )
    updated_at: datetime = Field(description="更新时间。")


class LogicTraceWriteResultDto(LogicTraceStoreDto):
    """LogicTraceStore 通用写入结果。"""

    status: LogicTraceWriteStatus = Field(description="写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="稳定错误码。",
    )
    retryable: bool = Field(default=False, description="是否允许补偿重试。")
    detail: str | None = Field(default=None, min_length=1, description="工程排障说明。")
    idempotent: bool = Field(default=False, description="是否命中幂等写入。")


class LogicTraceQueryResultDto(LogicTraceStoreDto):
    """LogicTraceStore 查询结果 DTO。"""

    traces: list[TraceDto] = Field(default_factory=list, description="逻辑链列表。")
    total: int = Field(ge=0, description="符合查询条件的总数。")


class LogicTraceStreamEventDto(LogicTraceStoreDto):
    """LogicTraceStore 实时流事件 DTO。"""

    stream_event_id: str = Field(min_length=1, description="流事件 ID。")
    trace_id: str = Field(min_length=1, description="逻辑链 ID。")
    projection_type: TraceProjectionType = Field(description="投影类型。")
    event_type: str = Field(min_length=1, description="事件类型。")
    display_payload: JsonMap = Field(default_factory=dict, description="展示负载。")
    created_at: datetime = Field(description="创建时间。")


class LogicTraceSchemaValidationResultDto(LogicTraceStoreDto):
    """业务 trace patch 校验结果 DTO。"""

    valid: bool = Field(description="校验是否通过。")
    degraded_flags: list[str] = Field(default_factory=list, description="降级标记。")
    normalized_business_payload: JsonMap = Field(
        default_factory=dict,
        description="标准化后的业务负载。",
    )
    schema_ref: str | None = Field(
        default=None, min_length=1, description="schema 引用。"
    )
    errors: list[str] = Field(default_factory=list, description="校验错误。")
    warnings: list[str] = Field(default_factory=list, description="校验告警。")


__all__: tuple[str, ...] = (
    "BuildTraceProjectionCommandDto",
    "AppendTraceEventCommandDto",
    "FinalizeTraceCommandDto",
    "GetTraceQueryDto",
    "ListTracesQueryDto",
    "LogicTraceQueryResultDto",
    "LogicTraceSchemaValidationResultDto",
    "LogicTraceStoreDto",
    "LogicTraceStoreSettings",
    "LogicTraceWriteResultDto",
    "RecordCallSummaryCommandDto",
    "RecordTraceArtifactCommandDto",
    "StartTraceCommandDto",
    "TraceArtifactDto",
    "TraceCallSummaryDto",
    "TraceDetailDto",
    "TraceDto",
    "TraceEventDto",
    "TraceOutboxDto",
    "TraceProjectionDto",
    "JsonMap",
)
