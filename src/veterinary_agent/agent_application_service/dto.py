##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/dto.py
# 作用: 定义 AgentApplicationService 应用内 DTO，覆盖入口命令、执行上下文、图运行请求、结果、事件与 Trace 契约。
# 边界: 仅承载应用编排层结构化数据，不执行 HTTP 映射、存储访问、图调度或兽医业务判断。
##################################################################################################

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.agent_application_service.enums import (
    AgentTraceDeliveryStatus,
    AgentTraceFinalStatus,
    AgentTurnStatus,
)

JsonMap: TypeAlias = dict[str, object]


class AgentApplicationDto(BaseModel):
    """AgentApplicationService DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class AgentTurnRequestContextDto(AgentApplicationDto):
    """应用层单轮请求上下文 DTO。"""

    request_id: str = Field(min_length=1, description="本次入口请求 ID。")
    trace_id: str = Field(min_length=1, description="本次全链路追踪 ID。")
    response_mode: str = Field(min_length=1, description="入口归一化后的响应模式。")
    received_at: datetime = Field(description="入口接收请求的服务端时间。")
    route_kind: str = Field(min_length=1, description="入口路由类型。")


class AgentTurnTrustedIdentityDto(AgentApplicationDto):
    """应用层可信身份上下文 DTO。"""

    user_id: str = Field(min_length=1, description="上游可信传入的用户 ID。")
    session_id: str = Field(min_length=1, description="上游可信传入的 session ID。")
    pet_id: str = Field(min_length=1, description="上游可信传入的宠物 ID。")
    pet_info: JsonMap | None = Field(
        default=None,
        description="客户端透传的宠物基础信息快照。",
    )


class AgentTurnInputTextDto(AgentApplicationDto):
    """应用层文本输入内容 DTO。"""

    type: Literal["input_text"] = Field(
        default="input_text",
        description="输入内容类型，固定为 input_text。",
    )
    text: str = Field(min_length=1, description="用户文本输入。")


class AgentTurnInputAttachmentDto(AgentApplicationDto):
    """应用层附件引用输入内容 DTO。"""

    type: Literal["input_attachment"] = Field(
        default="input_attachment",
        description="输入内容类型，固定为 input_attachment。",
    )
    attachment_id: str = Field(min_length=1, description="本轮附件引用 ID。")


AgentTurnInputContentDto: TypeAlias = Annotated[
    AgentTurnInputTextDto | AgentTurnInputAttachmentDto,
    Field(discriminator="type"),
]


class AgentTurnInputItemDto(AgentApplicationDto):
    """应用层输入项 DTO。"""

    type: str = Field(default="message", min_length=1, description="输入项类型。")
    role: str = Field(default="user", min_length=1, description="输入消息角色。")
    content: list[AgentTurnInputContentDto] = Field(
        min_length=1,
        description="输入内容数组。",
    )


class AgentTurnAttachmentDto(AgentApplicationDto):
    """应用层附件引用 DTO。"""

    attachment_id: str = Field(min_length=1, description="本轮附件 ID。")
    mime_type: str = Field(min_length=1, description="附件 MIME 类型。")
    purpose: str = Field(min_length=1, description="附件用途提示。")
    storage_ref: str = Field(min_length=1, description="附件外部存储引用。")
    metadata: JsonMap | None = Field(
        default=None,
        description="附件普通元信息。",
    )


class AgentTurnOptionsDto(AgentApplicationDto):
    """应用层单轮入口选项 DTO。"""

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="客户端显式传入的整轮幂等键。",
    )
    response_mode: str | None = Field(
        default=None,
        min_length=1,
        description="可选响应模式提示。",
    )


class AgentTurnExecutionOptionsDto(AgentApplicationDto):
    """单轮 Agent 应用编排执行选项 DTO。"""

    orchestrator_target: str = Field(
        min_length=1,
        description="编排入口目标标识。",
    )
    connect_timeout_seconds: float = Field(
        gt=0,
        description="连接编排入口的超时时间，单位为秒。",
    )
    request_timeout_seconds: float = Field(
        gt=0,
        description="同步调用编排层的请求超时时间，单位为秒。",
    )
    stream_first_event_timeout_seconds: float = Field(
        gt=0,
        description="等待流式首事件的超时时间，单位为秒。",
    )
    stream_total_timeout_seconds: float = Field(
        gt=0,
        description="流式调用允许的最大总时长，单位为秒。",
    )
    heartbeat_enabled: bool = Field(description="入口层是否允许发送 SSE 心跳。")
    heartbeat_interval_seconds: float = Field(
        gt=0,
        description="SSE 心跳间隔，单位为秒。",
    )
    stream_idle_timeout_seconds: float = Field(
        gt=0,
        description="SSE 空闲超时时间，单位为秒。",
    )
    max_stream_duration_seconds: float = Field(
        gt=0,
        description="单次流式连接最大持续时间，单位为秒。",
    )
    max_event_bytes: int = Field(
        ge=1,
        description="单个流式事件最大序列化字节数。",
    )
    client_cancel_notify_timeout_seconds: float = Field(
        gt=0,
        description="通知下游取消的等待时间，单位为秒。",
    )


class AgentTurnPublishCapabilitiesDto(AgentApplicationDto):
    """入口对本轮请求可承载的发布能力 DTO。"""

    supports_segments: bool = Field(
        default=True,
        description="入口是否支持业务分段输出。",
    )
    supports_reasoning_display: bool = Field(
        default=True,
        description="入口是否支持可展示推理摘要。",
    )
    supports_sse_events: bool = Field(
        description="当前请求是否支持 SSE 事件。",
    )


class AgentTurnDiagnosticsDto(AgentApplicationDto):
    """单轮请求入口诊断摘要 DTO。"""

    service_name: str = Field(min_length=1, description="入口服务名称。")
    environment: str = Field(min_length=1, description="当前运行环境标识。")
    config_version: str = Field(min_length=1, description="入口配置版本。")
    input_count: int = Field(ge=0, description="归一化输入项数量。")
    attachment_count: int = Field(ge=0, description="归一化附件数量。")


class AgentTurnRequestCommandDto(AgentApplicationDto):
    """AgentApplicationService 单轮执行命令 DTO。"""

    request_context: AgentTurnRequestContextDto = Field(description="入口请求上下文。")
    trusted_identity: AgentTurnTrustedIdentityDto = Field(
        description="上游可信身份上下文。",
    )
    input: list[AgentTurnInputItemDto] = Field(
        default_factory=list,
        description="归一化输入内容。",
    )
    attachments: list[AgentTurnAttachmentDto] = Field(
        default_factory=list,
        description="归一化附件引用。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通透传元信息。")
    model_hint: str | None = Field(
        default=None,
        min_length=1,
        description="客户端模型或模型策略提示。",
    )
    turn_options: AgentTurnOptionsDto | None = Field(
        default=None,
        description="本轮入口选项。",
    )
    idempotency_key: str = Field(min_length=1, description="整轮执行幂等键。")
    execution_options: AgentTurnExecutionOptionsDto = Field(
        description="应用编排执行选项。",
    )
    publish_capabilities: AgentTurnPublishCapabilitiesDto = Field(
        description="入口发布能力。",
    )
    diagnostics: AgentTurnDiagnosticsDto = Field(description="入口诊断摘要。")


class AgentTurnExecutionContextDto(AgentApplicationDto):
    """单轮 Agent 不可变执行上下文 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定单轮 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    session_id: str = Field(min_length=1, description="当前 session ID。")
    user_id: str = Field(min_length=1, description="当前用户 ID。")
    current_pet_id: str = Field(min_length=1, description="策略确认后的宠物 ID。")
    user_message_id: str = Field(min_length=1, description="已保存的用户消息 ID。")
    idempotency_key: str = Field(min_length=1, description="整轮幂等键。")
    params_version: str = Field(min_length=1, description="本轮业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="本轮配置快照 ID。")
    response_mode: str = Field(min_length=1, description="本轮响应模式。")
    route_kind: str = Field(min_length=1, description="本轮入口路由类型。")


class AgentGraphTurnRequestDto(AgentApplicationDto):
    """传递给 GraphRuntime 端口的单轮执行请求 DTO。"""

    context: AgentTurnExecutionContextDto = Field(description="不可变执行上下文。")
    input: list[AgentTurnInputItemDto] = Field(
        default_factory=list,
        description="归一化输入内容。",
    )
    attachments: list[AgentTurnAttachmentDto] = Field(
        default_factory=list,
        description="附件引用。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通透传元信息。")
    model_hint: str | None = Field(
        default=None,
        min_length=1,
        description="模型或模型策略提示。",
    )
    execution_options: AgentTurnExecutionOptionsDto = Field(
        description="执行超时与流式选项。",
    )
    publish_capabilities: AgentTurnPublishCapabilitiesDto = Field(
        description="入口发布能力。",
    )


class AgentReferenceDto(AgentApplicationDto):
    """应用层对外引用摘要 DTO。"""

    source_id: str | None = Field(default=None, min_length=1, description="来源 ID。")
    title: str | None = Field(default=None, min_length=1, description="来源标题。")
    uri: str | None = Field(default=None, min_length=1, description="来源地址或引用。")
    metadata: JsonMap | None = Field(default=None, description="普通引用元信息。")


class AgentReasoningDisplayDto(AgentApplicationDto):
    """应用层可展示推理摘要 DTO。"""

    projection_id: str = Field(min_length=1, description="投影 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="关联 segment ID。",
    )
    title: str | None = Field(default=None, min_length=1, description="展示标题。")
    text: str = Field(min_length=1, description="允许向用户展示的推理摘要。")
    metadata: JsonMap | None = Field(default=None, description="普通投影元信息。")


class AgentResponseSegmentDto(AgentApplicationDto):
    """应用层用户可见业务分段 DTO。"""

    segment_id: str = Field(min_length=1, description="稳定 segment ID。")
    type: str = Field(min_length=1, description="业务分段类型。")
    title: str | None = Field(default=None, min_length=1, description="业务分段标题。")
    status: str = Field(default="completed", min_length=1, description="分段状态。")
    output_text: str | None = Field(default=None, description="分段完整文本。")
    references: list[AgentReferenceDto] = Field(
        default_factory=list,
        description="分段引用摘要。",
    )
    reasoning_display: AgentReasoningDisplayDto | None = Field(
        default=None,
        description="分段可展示推理摘要。",
    )
    metadata: JsonMap | None = Field(default=None, description="分段普通元信息。")


class AgentVetResultDto(AgentApplicationDto):
    """应用层兽医业务对外摘要 DTO。"""

    generation_profile: str | None = Field(
        default=None,
        min_length=1,
        description="生成剖面摘要。",
    )
    route: str | None = Field(default=None, min_length=1, description="业务路由摘要。")
    audit_tier: str | None = Field(
        default=None,
        min_length=1,
        description="逻辑链审计分级摘要。",
    )
    metadata: JsonMap | None = Field(default=None, description="普通业务元信息。")


class AgentGraphTurnResultDto(AgentApplicationDto):
    """GraphRuntime 返回给应用服务的最终结果 DTO。"""

    output_text: str = Field(default="", description="整轮用户可见聚合正文。")
    segments: list[AgentResponseSegmentDto] = Field(
        default_factory=list,
        description="已发布业务分段。",
    )
    reasoning_display: AgentReasoningDisplayDto | None = Field(
        default=None,
        description="整轮可展示推理摘要。",
    )
    vet_result: AgentVetResultDto | None = Field(
        default=None,
        description="兽医业务对外摘要。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="图运行普通元信息。")


class AgentTurnResultDto(AgentApplicationDto):
    """AgentApplicationService 同步执行结果 DTO。"""

    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    created_at: datetime = Field(description="结果创建时间。")
    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    user_message_id: str = Field(min_length=1, description="用户消息 ID。")
    status: AgentTurnStatus = Field(description="整轮执行状态。")
    output_text: str = Field(default="", description="整轮用户可见正文。")
    segments: list[AgentResponseSegmentDto] = Field(
        default_factory=list,
        description="业务分段结果。",
    )
    reasoning_display: AgentReasoningDisplayDto | None = Field(
        default=None,
        description="整轮可展示推理摘要。",
    )
    vet_result: AgentVetResultDto | None = Field(
        default=None,
        description="兽医业务对外摘要。",
    )
    trace_delivery_status: AgentTraceDeliveryStatus = Field(
        description="逻辑链交付状态。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通结果元信息。")


class AgentGraphEventDto(AgentApplicationDto):
    """GraphRuntime 端口输出的协议无关运行事件 DTO。"""

    event_id: str = Field(min_length=1, description="图运行事件 ID。")
    event_type: str = Field(min_length=1, description="图运行事件类型。")
    data: JsonMap = Field(default_factory=dict, description="安全事件数据。")
    created_at: datetime = Field(description="事件创建时间。")


class AgentTurnEventDto(AgentApplicationDto):
    """AgentApplicationService 输出的协议无关流式事件 DTO。"""

    event_id: str = Field(min_length=1, description="应用事件 ID。")
    sequence_no: int = Field(ge=1, description="本轮单调递增事件序号。")
    event_type: str = Field(min_length=1, description="应用事件类型。")
    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    data: JsonMap = Field(default_factory=dict, description="安全事件数据。")
    created_at: datetime = Field(description="事件创建时间。")


class AgentResumeTurnCommandDto(AgentApplicationDto):
    """恢复未完成 Agent 运行的命令 DTO。"""

    request_id: str = Field(min_length=1, description="恢复请求 ID。")
    trace_id: str = Field(min_length=1, description="既有逻辑链 ID。")
    run_id: str = Field(min_length=1, description="需要恢复的图运行 ID。")
    checkpoint_ref: str | None = Field(
        default=None,
        min_length=1,
        description="可选 checkpoint 引用。",
    )


class AgentCancelTurnCommandDto(AgentApplicationDto):
    """取消 Agent 运行的命令 DTO。"""

    request_id: str = Field(min_length=1, description="取消请求 ID。")
    trace_id: str = Field(min_length=1, description="既有逻辑链 ID。")
    run_id: str = Field(min_length=1, description="需要取消的图运行 ID。")
    reason: str = Field(min_length=1, description="取消原因摘要。")


class AgentCancelTurnResultDto(AgentApplicationDto):
    """取消 Agent 运行的结果 DTO。"""

    run_id: str = Field(min_length=1, description="已处理的图运行 ID。")
    cancelled: bool = Field(description="是否已完成取消。")
    idempotent: bool = Field(description="是否命中既有取消状态。")


class AgentTraceStartCommandDto(AgentApplicationDto):
    """启动整轮逻辑链的命令 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="入口生成或透传的逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    session_id: str = Field(min_length=1, description="当前 session ID。")
    user_id: str = Field(min_length=1, description="当前用户 ID。")
    pet_id: str = Field(min_length=1, description="入口显式携带的宠物 ID。")
    params_version: str = Field(min_length=1, description="本轮业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="本轮配置快照 ID。")
    idempotency_key: str = Field(min_length=1, description="整轮幂等键。")


class AgentTraceFinalizeCommandDto(AgentApplicationDto):
    """完成整轮逻辑链的命令 DTO。"""

    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str = Field(min_length=1, description="稳定 turn ID。")
    run_id: str = Field(min_length=1, description="稳定图运行 ID。")
    final_status: AgentTraceFinalStatus = Field(description="逻辑链最终状态。")
    user_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="已保存的用户消息 ID。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="失败时的应用层稳定错误码。",
    )
    summary: JsonMap = Field(default_factory=dict, description="最终安全摘要。")


class AgentTraceWriteResultDto(AgentApplicationDto):
    """逻辑链启动或完成写入结果 DTO。"""

    status: AgentTraceDeliveryStatus = Field(description="逻辑链交付状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="降级或失败时的稳定错误码。",
    )
    retryable: bool = Field(default=False, description="是否允许补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="工程排障摘要。",
    )


__all__: tuple[str, ...] = (
    "AgentApplicationDto",
    "AgentCancelTurnCommandDto",
    "AgentCancelTurnResultDto",
    "AgentGraphEventDto",
    "AgentGraphTurnRequestDto",
    "AgentGraphTurnResultDto",
    "AgentReasoningDisplayDto",
    "AgentReferenceDto",
    "AgentResponseSegmentDto",
    "AgentResumeTurnCommandDto",
    "AgentTraceFinalizeCommandDto",
    "AgentTraceStartCommandDto",
    "AgentTraceWriteResultDto",
    "AgentTurnAttachmentDto",
    "AgentTurnDiagnosticsDto",
    "AgentTurnEventDto",
    "AgentTurnExecutionContextDto",
    "AgentTurnExecutionOptionsDto",
    "AgentTurnInputAttachmentDto",
    "AgentTurnInputContentDto",
    "AgentTurnInputItemDto",
    "AgentTurnInputTextDto",
    "AgentTurnOptionsDto",
    "AgentTurnPublishCapabilitiesDto",
    "AgentTurnRequestCommandDto",
    "AgentTurnRequestContextDto",
    "AgentTurnResultDto",
    "AgentTurnTrustedIdentityDto",
    "AgentVetResultDto",
    "JsonMap",
)
