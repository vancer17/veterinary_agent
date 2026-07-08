##################################################################################################
# 文件: src/veterinary_agent/api_ingress/dto.py
# 作用: 定义 API 接入组件使用的 DTO 模型，覆盖外部请求、内部归一化请求、同步响应、SSE 事件、错误响应和探针响应。
# 边界: 仅描述 ApiIngress 协议接入层的数据承载结构，不包含兽医业务判断、OCR、RAG、安全审查或逻辑链 DTO。
##################################################################################################

from datetime import datetime
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from veterinary_agent.api_ingress.enums import (
    ApiRouteKind,
    IngressErrorCode,
    InputContentType,
    InputItemType,
    InputRole,
    OutputContentType,
    ResponseMode,
    SegmentStatus,
    SseEventType,
    TurnStatus,
)

JsonMap: TypeAlias = dict[str, object]


class ApiIngressDto(BaseModel):
    """API 接入组件 DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class VetContextDto(ApiIngressDto):
    """兽医业务上下文 DTO。"""

    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID；ApiIngress 不执行用户鉴权。",
    )
    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID；一 session 一宠策略由下游业务组件校验。",
    )
    pet_id: str = Field(
        min_length=1,
        description="上游可信传入的本轮宠物 ID；ApiIngress 不推断、不纠正、不切换宠物。",
    )
    pet_info: JsonMap | None = Field(
        default=None,
        description="客户端透传的宠物基础信息；真实性与补全由下游上下文组件处理。",
    )


class InputTextContentDto(ApiIngressDto):
    """文本输入内容 DTO。"""

    type: Literal[InputContentType.INPUT_TEXT] = Field(
        default=InputContentType.INPUT_TEXT,
        description="输入内容类型，固定为 input_text。",
    )
    text: str = Field(
        min_length=1,
        description="用户文本输入内容。",
    )


class InputAttachmentContentDto(ApiIngressDto):
    """附件引用输入内容 DTO。"""

    type: Literal[InputContentType.INPUT_ATTACHMENT] = Field(
        default=InputContentType.INPUT_ATTACHMENT,
        description="输入内容类型，固定为 input_attachment。",
    )
    attachment_id: str = Field(
        min_length=1,
        description="本轮请求内的附件 ID，必须引用 attachments 中的附件元信息。",
    )


InputContentDto: TypeAlias = Annotated[
    InputTextContentDto | InputAttachmentContentDto,
    Field(discriminator="type"),
]


class InputItemDto(ApiIngressDto):
    """外部输入项 DTO。"""

    type: InputItemType = Field(
        default=InputItemType.MESSAGE,
        description="输入项类型；当前外部 API 仅支持 message。",
    )
    role: InputRole = Field(
        default=InputRole.USER,
        description="输入消息角色；当前外部 API 仅允许 user。",
    )
    content: list[InputContentDto] = Field(
        min_length=1,
        description="输入内容数组，当前支持文本输入与附件引用。",
    )


class AttachmentRefDto(ApiIngressDto):
    """附件引用元信息 DTO。"""

    attachment_id: str = Field(
        min_length=1,
        description="附件 ID；在本轮请求中应保持唯一。",
    )
    mime_type: str = Field(
        min_length=1,
        description="附件 MIME 类型；ApiIngress 仅做入口格式与限制校验。",
    )
    purpose: str = Field(
        min_length=1,
        description="附件用途提示；入口层不据此判断附件是否可作为医学依据。",
    )
    storage_ref: str = Field(
        min_length=1,
        description="上游文件服务或对象存储引用；本接口不接收附件二进制或 base64 内容。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="附件普通元信息；不得承载安全绕过或工具授权语义。",
    )


class TurnOptionsDto(ApiIngressDto):
    """单轮入口选项 DTO。"""

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="客户端传入的幂等键；整轮幂等判定由编排层或会话持久化层负责。",
    )
    response_mode: ResponseMode | None = Field(
        default=None,
        description="响应模式提示；与顶层 stream 冲突时应以 stream 为准。",
    )


class AgentTurnRequestDto(ApiIngressDto):
    """外部一轮 Agent 对话请求 DTO。"""

    request_id: str | None = Field(
        default=None,
        min_length=1,
        description="上游请求 ID；不传时由 ApiIngress 根据配置生成。",
    )
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        description="上游链路 ID；不传时由 ApiIngress 根据配置生成。",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="模型或模型策略提示；最终模型选择仍由服务端配置和编排层决定。",
    )
    input: list[InputItemDto] | None = Field(
        default=None,
        min_length=1,
        description="本轮输入内容；与 attachments 至少存在一类有效内容。",
    )
    stream: StrictBool | None = Field(
        default=None,
        description="是否启用 SSE 流式响应；未传时采用服务默认响应模式。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="客户端普通透传元信息；不得承载安全绕过、工具授权或策略覆盖语义。",
    )
    vet_context: VetContextDto = Field(
        description="兽医业务上下文；ApiIngress 仅校验必需字段存在。",
    )
    attachments: list[AttachmentRefDto] | None = Field(
        default=None,
        min_length=1,
        description="附件引用元信息；当前接口只接收引用，不接收二进制或 base64 内容。",
    )
    turn_options: TurnOptionsDto | None = Field(
        default=None,
        description="本轮入口选项。",
    )

    @model_validator(mode="after")
    def _validate_input_or_attachments(self) -> Self:
        """校验请求至少包含输入或附件。

        :return: 通过校验后的当前请求 DTO。
        :raises ValueError: 当 input 与 attachments 均为空时抛出。
        """

        if not self.input and not self.attachments:
            raise ValueError("input 与 attachments 至少存在一类有效内容")
        return self

    @model_validator(mode="after")
    def _validate_unique_attachment_ids(self) -> Self:
        """校验本轮请求内附件 ID 唯一。

        :return: 通过校验后的当前请求 DTO。
        :raises ValueError: 当 attachments 中存在重复 attachment_id 时抛出。
        """

        if not self.attachments:
            return self
        attachment_ids = [attachment.attachment_id for attachment in self.attachments]
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("attachments 中的 attachment_id 不得重复")
        return self


class RequestContextDto(ApiIngressDto):
    """入口请求上下文 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    response_mode: ResponseMode = Field(
        description="入口层归一化后的响应模式。",
    )
    received_at: datetime = Field(
        description="入口层接收请求的服务端时间。",
    )
    route_kind: ApiRouteKind = Field(
        description="入口层归一化后的路由类型。",
    )


class TrustedIdentityDto(ApiIngressDto):
    """上游可信身份上下文 DTO。"""

    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="上游可信传入的宠物 ID。",
    )
    pet_info: JsonMap | None = Field(
        default=None,
        description="客户端透传的宠物基础信息。",
    )


class AgentTurnInternalRequestDto(ApiIngressDto):
    """ApiIngress 转发给编排层的内部归一化请求 DTO。"""

    request_context: RequestContextDto = Field(
        description="入口请求上下文。",
    )
    trusted_identity: TrustedIdentityDto = Field(
        description="上游可信身份上下文。",
    )
    input: list[InputItemDto] = Field(
        default_factory=list,
        description="归一化后的输入内容列表。",
    )
    attachments: list[AttachmentRefDto] = Field(
        default_factory=list,
        description="归一化后的附件引用元信息列表。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="归一化后的普通透传元信息。",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="模型或模型策略提示。",
    )
    turn_options: TurnOptionsDto | None = Field(
        default=None,
        description="本轮入口选项。",
    )


class OutputTextContentDto(ApiIngressDto):
    """完整文本输出内容 DTO。"""

    type: Literal[OutputContentType.OUTPUT_TEXT] = Field(
        default=OutputContentType.OUTPUT_TEXT,
        description="输出内容类型，固定为 output_text。",
    )
    text: str = Field(
        description="完整文本输出。",
    )


class OutputTextDeltaDto(ApiIngressDto):
    """文本增量输出内容 DTO。"""

    type: Literal[OutputContentType.OUTPUT_TEXT_DELTA] = Field(
        default=OutputContentType.OUTPUT_TEXT_DELTA,
        description="输出内容类型，固定为 output_text_delta。",
    )
    text: str = Field(
        description="流式文本增量。",
    )


OutputContentDto: TypeAlias = Annotated[
    OutputTextContentDto | OutputTextDeltaDto,
    Field(discriminator="type"),
]


class OutputItemDto(ApiIngressDto):
    """同步响应输出项 DTO。"""

    type: str = Field(
        default="message",
        min_length=1,
        description="输出项类型；当前同步响应通常为 message。",
    )
    role: str = Field(
        default="assistant",
        min_length=1,
        description="输出消息角色；当前同步响应通常为 assistant。",
    )
    content: list[OutputContentDto] = Field(
        default_factory=list,
        description="输出内容数组。",
    )


class ReferenceDto(ApiIngressDto):
    """对外引用摘要 DTO。"""

    source_id: str | None = Field(
        default=None,
        min_length=1,
        description="引用来源 ID。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description="引用标题。",
    )
    uri: str | None = Field(
        default=None,
        min_length=1,
        description="引用资源地址或内部引用标识。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="引用普通元信息；不得包含完整受限原文。",
    )


class ReasoningDisplayDto(ApiIngressDto):
    """可展示推理摘要 DTO。"""

    projection_id: str = Field(
        min_length=1,
        description="可展示 reasoning display 投影 ID，用于前端定位和内部排障关联。",
    )
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="关联的业务分段 ID；整轮摘要可为空。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description="前端折叠区标题，由下游组件产出，ApiIngress 仅透传。",
    )
    text: str = Field(
        min_length=1,
        description="已经由下游生成、裁剪并允许展示的 reasoning display 文本。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="reasoning display 普通扩展信息；不得包含隐藏思维链、完整 trace、审查三联稿或受限原文。",
    )


class SegmentDto(ApiIngressDto):
    """业务分段对外承载 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="业务分段 ID。",
    )
    type: str = Field(
        min_length=1,
        description="业务分段类型；由下游回复合成组件产生，ApiIngress 不解释其业务含义。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description="业务分段标题。",
    )
    status: SegmentStatus = Field(
        description="业务分段发布状态。",
    )
    output_text: str | None = Field(
        default=None,
        description="业务分段完整文本。",
    )
    references: list[ReferenceDto] = Field(
        default_factory=list,
        description="业务分段引用摘要列表。",
    )
    reasoning_display: ReasoningDisplayDto | None = Field(
        default=None,
        description="关联本业务分段的可展示 reasoning display；ApiIngress 仅透传。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="业务分段普通元信息。",
    )


class VetResultDto(ApiIngressDto):
    """对外兽医业务摘要 DTO。"""

    generation_profile: str | None = Field(
        default=None,
        min_length=1,
        description="下游产生的生成剖面摘要；ApiIngress 仅透传。",
    )
    route: str | None = Field(
        default=None,
        min_length=1,
        description="下游产生的路由摘要；ApiIngress 仅透传。",
    )
    audit_tier: str | None = Field(
        default=None,
        min_length=1,
        description="下游产生的审计分级摘要；ApiIngress 仅透传。",
    )
    metadata: JsonMap | None = Field(
        default=None,
        description="对外业务摘要普通元信息。",
    )


class AgentTurnResponseDto(ApiIngressDto):
    """同步一轮 Agent 对话响应 DTO。"""

    id: str = Field(
        min_length=1,
        description="本轮 turn ID。",
    )
    object: Literal["agent.turn"] = Field(
        default="agent.turn",
        description="响应资源类型，固定为 agent.turn。",
    )
    created_at: datetime = Field(
        description="服务端创建响应的时间。",
    )
    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    status: TurnStatus = Field(
        description="本轮 turn 生命周期状态。",
    )
    output: list[OutputItemDto] = Field(
        default_factory=list,
        description="OpenAI Responses 风格输出内容数组。",
    )
    segments: list[SegmentDto] = Field(
        default_factory=list,
        description="兽医业务分段结果数组。",
    )
    reasoning_display: ReasoningDisplayDto | None = Field(
        default=None,
        description="整轮可展示 reasoning display；ApiIngress 仅透传下游已允许展示的文本。",
    )
    vet_result: VetResultDto | None = Field(
        default=None,
        description="对外兽医业务结构化摘要。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="响应普通元信息。",
    )


class SseEventDto(ApiIngressDto):
    """通用 SSE 事件 DTO。"""

    event: SseEventType = Field(
        description="SSE 事件类型。",
    )
    data: JsonMap = Field(
        default_factory=dict,
        description="SSE 事件数据；具体结构可由专用事件 data DTO 承载。",
    )


class TurnStartedEventDataDto(ApiIngressDto):
    """turn.started 事件数据 DTO。"""

    id: str = Field(
        min_length=1,
        description="本轮 turn ID。",
    )
    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )


class ReasoningDisplayStartedEventDataDto(ApiIngressDto):
    """reasoning_display.started 事件数据 DTO。"""

    projection_id: str = Field(
        min_length=1,
        description="可展示 reasoning display 投影 ID。",
    )
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="关联的业务分段 ID；整轮摘要可为空。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description="前端折叠区标题，由下游组件产出。",
    )


class ReasoningDisplayDeltaEventDataDto(ApiIngressDto):
    """reasoning_display.delta 事件数据 DTO。"""

    projection_id: str = Field(
        min_length=1,
        description="可展示 reasoning display 投影 ID。",
    )
    text_delta: str = Field(
        min_length=1,
        description="已经由下游允许展示的 reasoning display 文本增量。",
    )


class ReasoningDisplayCompletedEventDataDto(ApiIngressDto):
    """reasoning_display.completed 事件数据 DTO。"""

    reasoning_display: ReasoningDisplayDto = Field(
        description="完整的可展示 reasoning display。",
    )


class SegmentStartedEventDataDto(ApiIngressDto):
    """segment.started 事件数据 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="业务分段 ID。",
    )
    index: int = Field(
        ge=0,
        description="业务分段在本轮响应中的顺序索引。",
    )
    type: str = Field(
        min_length=1,
        description="业务分段类型；ApiIngress 不解释其业务含义。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        description="业务分段标题。",
    )


class SegmentDeltaEventDataDto(ApiIngressDto):
    """segment.delta 事件数据 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="业务分段 ID。",
    )
    delta: OutputTextDeltaDto = Field(
        description="业务分段文本增量。",
    )


class SegmentCompletedEventDataDto(ApiIngressDto):
    """segment.completed 事件数据 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="业务分段 ID。",
    )
    status: SegmentStatus = Field(
        default=SegmentStatus.COMPLETED,
        description="业务分段完成状态。",
    )


class TurnCompletedEventDataDto(ApiIngressDto):
    """turn.completed 事件数据 DTO。"""

    id: str = Field(
        min_length=1,
        description="本轮 turn ID。",
    )
    status: TurnStatus = Field(
        default=TurnStatus.COMPLETED,
        description="本轮 turn 完成状态。",
    )


class TurnFailedEventDataDto(ApiIngressDto):
    """turn.failed 事件数据 DTO。"""

    id: str | None = Field(
        default=None,
        min_length=1,
        description="本轮 turn ID；若失败发生在创建 turn 前可为空。",
    )
    code: IngressErrorCode | str = Field(
        description="失败错误码；入口层错误优先使用 IngressErrorCode。",
    )
    message: str = Field(
        min_length=1,
        description="面向研发的失败说明。",
    )


class HeartbeatEventDataDto(ApiIngressDto):
    """heartbeat 事件数据 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )


class ErrorDetailDto(ApiIngressDto):
    """错误明细 DTO。"""

    field: str | None = Field(
        default=None,
        min_length=1,
        description="发生错误的字段路径。",
    )
    reason: str = Field(
        min_length=1,
        description="字段级或依赖级错误原因。",
    )


class ErrorResponseDto(ApiIngressDto):
    """统一错误响应 DTO。"""

    code: IngressErrorCode = Field(
        description="机器可读入口错误码。",
    )
    message: str = Field(
        min_length=1,
        description="面向研发的错误说明。",
    )
    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    details: list[ErrorDetailDto] | None = Field(
        default=None,
        description="字段级或依赖级错误明细。",
    )


class HealthResponseDto(ApiIngressDto):
    """存活检查响应 DTO。"""

    status: Literal["ok"] = Field(
        default="ok",
        description="进程存活状态，固定为 ok。",
    )


class ReadyResponseDto(ApiIngressDto):
    """就绪检查成功响应 DTO。"""

    status: Literal["ready"] = Field(
        default="ready",
        description="服务就绪状态，固定为 ready。",
    )


__all__: tuple[str, ...] = (
    "AgentTurnInternalRequestDto",
    "AgentTurnRequestDto",
    "AgentTurnResponseDto",
    "ApiIngressDto",
    "AttachmentRefDto",
    "ErrorDetailDto",
    "ErrorResponseDto",
    "HeartbeatEventDataDto",
    "HealthResponseDto",
    "InputAttachmentContentDto",
    "InputContentDto",
    "InputItemDto",
    "InputTextContentDto",
    "OutputContentDto",
    "OutputItemDto",
    "OutputTextContentDto",
    "OutputTextDeltaDto",
    "ReadyResponseDto",
    "ReferenceDto",
    "ReasoningDisplayCompletedEventDataDto",
    "ReasoningDisplayDeltaEventDataDto",
    "ReasoningDisplayDto",
    "ReasoningDisplayStartedEventDataDto",
    "RequestContextDto",
    "SegmentCompletedEventDataDto",
    "SegmentDeltaEventDataDto",
    "SegmentDto",
    "SegmentStartedEventDataDto",
    "SseEventDto",
    "TrustedIdentityDto",
    "TurnCompletedEventDataDto",
    "TurnFailedEventDataDto",
    "TurnOptionsDto",
    "TurnStartedEventDataDto",
    "VetContextDto",
    "VetResultDto",
)
