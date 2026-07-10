##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/dto.py
# 作用: 定义 LlmGateway 公共调用契约、ProviderAdapter 内部契约、流式事件与脱敏调用摘要。
# 边界: 仅承载协议无关数据结构，不执行网络请求、重试、降级、业务 prompt 构造或持久化。
##################################################################################################

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from veterinary_agent.llm_gateway.enums import (
    LlmContentPartType,
    LlmFinishReason,
    LlmGatewayErrorCode,
    LlmGatewayOperation,
    LlmMessageRole,
    LlmResponseFormatType,
    LlmStreamEventType,
    LlmTraceWriteStatus,
    ProviderStreamEventType,
)

JsonMap: TypeAlias = dict[str, object]

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_RESERVED_GENERATION_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "messages",
        "metadata",
        "model",
        "response_format",
        "stream",
        "stream_options",
        "tools",
    }
)


class LlmGatewayDto(BaseModel):
    """LlmGateway DTO 严格模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class LlmTextContentPartDto(LlmGatewayDto):
    """模型文本内容分片。"""

    type: Literal[LlmContentPartType.TEXT] = Field(
        default=LlmContentPartType.TEXT,
        description="内容分片类型。",
    )
    text: str = Field(
        min_length=1,
        description="文本内容。",
    )


class LlmImageUrlDto(LlmGatewayDto):
    """模型视觉内容地址。"""

    url: str = Field(
        min_length=1,
        max_length=4_000_000,
        description="图片 URL 或受控 data URL。",
    )
    detail: Literal["auto", "low", "high"] = Field(
        default="auto",
        description="模型视觉解析精度提示。",
    )


class LlmImageContentPartDto(LlmGatewayDto):
    """模型视觉内容分片。"""

    type: Literal[LlmContentPartType.IMAGE_URL] = Field(
        default=LlmContentPartType.IMAGE_URL,
        description="内容分片类型。",
    )
    image_url: LlmImageUrlDto = Field(
        description="图片地址及解析精度。",
    )


LlmContentPartDto: TypeAlias = Annotated[
    LlmTextContentPartDto | LlmImageContentPartDto,
    Field(discriminator="type"),
]


class LlmFunctionCallDto(LlmGatewayDto):
    """模型工具函数调用。"""

    name: str = Field(
        min_length=1,
        max_length=128,
        description="工具函数名称。",
    )
    arguments: str = Field(
        description="模型返回的 JSON 参数字符串。",
    )

    @field_validator("name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        """校验工具函数名称。

        :param value: 原始工具函数名称。
        :return: 通过格式校验的工具函数名称。
        :raises ValueError: 当工具函数名称包含非法字符时抛出。
        """

        if _TOOL_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("工具函数名称仅允许字母、数字、下划线和连字符")
        return value


class LlmToolCallDto(LlmGatewayDto):
    """模型完整工具调用。"""

    id: str = Field(
        min_length=1,
        max_length=256,
        description="供应商返回的工具调用 ID。",
    )
    type: Literal["function"] = Field(
        default="function",
        description="工具调用类型。",
    )
    function: LlmFunctionCallDto = Field(
        description="工具函数调用信息。",
    )


class LlmToolCallDeltaDto(LlmGatewayDto):
    """模型流式工具调用增量。"""

    index: int = Field(
        ge=0,
        description="工具调用在当前回复中的索引。",
    )
    id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="工具调用 ID 增量。",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="工具函数名称增量。",
    )
    arguments_delta: str = Field(
        default="",
        description="工具函数参数字符串增量。",
    )


class LlmMessageDto(LlmGatewayDto):
    """协议无关模型消息。"""

    role: LlmMessageRole = Field(
        description="消息角色。",
    )
    content: str | list[LlmContentPartDto] | None = Field(
        default=None,
        description="文本或多模态消息内容。",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="可选消息发送者名称。",
    )
    tool_call_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="工具结果消息关联的工具调用 ID。",
    )
    tool_calls: list[LlmToolCallDto] = Field(
        default_factory=list,
        description="助手历史消息中已经产生的工具调用。",
    )

    @model_validator(mode="after")
    def _validate_message_shape(self) -> "LlmMessageDto":
        """校验不同消息角色的字段组合。

        :return: 已通过角色字段关系校验的消息。
        :raises ValueError: 当消息内容为空或工具消息缺少调用 ID 时抛出。
        """

        if self.content is None and not self.tool_calls:
            raise ValueError("消息必须包含 content 或 tool_calls")
        if isinstance(self.content, list) and not self.content:
            raise ValueError("多模态消息内容列表不得为空")
        if self.role is LlmMessageRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool 消息必须提供 tool_call_id")
        if self.role is not LlmMessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("仅 assistant 消息可以携带 tool_calls")
        return self


class LlmToolFunctionSchemaDto(LlmGatewayDto):
    """模型可调用工具函数 schema。"""

    name: str = Field(
        min_length=1,
        max_length=128,
        description="工具函数名称。",
    )
    description: str | None = Field(
        default=None,
        max_length=4096,
        description="供模型理解工具用途的说明。",
    )
    parameters: JsonMap = Field(
        default_factory=dict,
        description="JSON Schema 形式的工具参数定义。",
    )
    strict: bool | None = Field(
        default=None,
        description="是否要求供应商严格遵守工具参数 schema。",
    )

    @field_validator("name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        """校验工具 schema 名称。

        :param value: 原始工具函数名称。
        :return: 通过格式校验的工具函数名称。
        :raises ValueError: 当工具函数名称包含非法字符时抛出。
        """

        if _TOOL_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("工具函数名称仅允许字母、数字、下划线和连字符")
        return value


class LlmToolSchemaDto(LlmGatewayDto):
    """模型可调用工具 schema。"""

    type: Literal["function"] = Field(
        default="function",
        description="工具 schema 类型。",
    )
    function: LlmToolFunctionSchemaDto = Field(
        description="工具函数 schema。",
    )


class LlmJsonSchemaDto(LlmGatewayDto):
    """模型 JSON Schema 响应约束。"""

    name: str = Field(
        min_length=1,
        max_length=128,
        description="响应 schema 名称。",
    )
    description: str | None = Field(
        default=None,
        max_length=4096,
        description="响应 schema 用途说明。",
    )
    schema_: JsonMap = Field(
        alias="schema",
        serialization_alias="schema",
        description="JSON Schema 定义。",
    )
    strict: bool = Field(
        default=True,
        description="是否要求供应商严格遵守响应 schema。",
    )


class LlmResponseFormatDto(LlmGatewayDto):
    """模型响应格式要求。"""

    type: LlmResponseFormatType = Field(
        default=LlmResponseFormatType.TEXT,
        description="响应格式类型。",
    )
    json_schema: LlmJsonSchemaDto | None = Field(
        default=None,
        description="JSON Schema 响应约束。",
    )

    @model_validator(mode="after")
    def _validate_response_format(self) -> "LlmResponseFormatDto":
        """校验响应格式与 JSON Schema 字段关系。

        :return: 已通过字段关系校验的响应格式。
        :raises ValueError: 当 JSON Schema 类型缺少 schema 或其他类型携带 schema 时抛出。
        """

        if self.type is LlmResponseFormatType.JSON_SCHEMA and self.json_schema is None:
            raise ValueError("json_schema 响应格式必须提供 json_schema")
        if (
            self.type is not LlmResponseFormatType.JSON_SCHEMA
            and self.json_schema is not None
        ):
            raise ValueError("仅 json_schema 响应格式可以提供 json_schema")
        return self


class LlmUsageSummaryDto(LlmGatewayDto):
    """模型 token 使用摘要。"""

    input_tokens: int = Field(
        default=0,
        ge=0,
        description="输入 token 数。",
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="输出 token 数。",
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="总 token 数。",
    )
    estimated: bool = Field(
        default=False,
        description="当前 token 数是否为本地估算值。",
    )

    @model_validator(mode="after")
    def _normalize_total_tokens(self) -> "LlmUsageSummaryDto":
        """补齐并校验总 token 数。

        :return: 已补齐总量的 token 使用摘要。
        :raises ValueError: 当供应商总量小于输入与输出之和时抛出。
        """

        calculated_total = self.input_tokens + self.output_tokens
        if self.total_tokens == 0 and calculated_total > 0:
            self.total_tokens = calculated_total
        if self.total_tokens < calculated_total:
            raise ValueError("total_tokens 不得小于 input_tokens 与 output_tokens 之和")
        return self


class LlmErrorDto(LlmGatewayDto):
    """LlmGateway 统一错误 DTO。"""

    code: LlmGatewayErrorCode = Field(
        description="LlmGateway 稳定错误码。",
    )
    operation: LlmGatewayOperation = Field(
        description="发生错误的 LlmGateway 操作名。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明。",
    )
    retryable: bool = Field(
        description="调用方是否可以稍后重试。",
    )
    call_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前模型逻辑调用 ID。",
    )
    request_id: str | None = Field(
        default=None,
        min_length=1,
        description="入口请求 ID。",
    )
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        description="全链路追踪 ID。",
    )
    model_profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="发生错误的模型 profile ID。",
    )
    provider_route_id: str | None = Field(
        default=None,
        min_length=1,
        description="发生错误的供应商路由 ID。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="不含敏感正文的冲突或下游状态摘要。",
    )


class LlmInvocationRequestDto(LlmGatewayDto):
    """一次协议无关模型调用请求。"""

    call_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="可选调用方指定的逻辑模型调用 ID；为空时由 LlmGateway 生成。",
    )
    trace_id: str = Field(
        min_length=1,
        max_length=128,
        description="全链路追踪 ID。",
    )
    request_id: str = Field(
        min_length=1,
        max_length=128,
        description="入口请求 ID。",
    )
    caller_component: str = Field(
        min_length=1,
        max_length=128,
        description="调用 LlmGateway 的系统组件名。",
    )
    model_profile_id: str = Field(
        min_length=1,
        max_length=128,
        description="本次调用使用的稳定模型 profile ID。",
    )
    messages: list[LlmMessageDto] = Field(
        min_length=1,
        description="由 AgentRunner 准备完成的模型消息。",
    )
    response_format: LlmResponseFormatDto = Field(
        default_factory=LlmResponseFormatDto,
        description="模型响应格式要求。",
    )
    tool_schemas: list[LlmToolSchemaDto] = Field(
        default_factory=list,
        description="已经过 ToolRegistry 授权的工具 schema。",
    )
    stream: bool = Field(
        default=False,
        description="是否请求流式模型响应。",
    )
    generation_params: JsonMap = Field(
        default_factory=dict,
        description="允许透传给模型代理的生成参数。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="仅供调用摘要与观测使用的安全元数据，不直接透传给供应商。",
    )

    @field_validator(
        "call_id",
        "trace_id",
        "request_id",
        "caller_component",
        "model_profile_id",
    )
    @classmethod
    def _validate_identity_value(cls, value: str | None) -> str | None:
        """校验调用关联字段可安全进入请求头与日志。

        :param value: 原始关联字段值。
        :return: 通过格式校验的关联字段值或空值。
        :raises ValueError: 当字段包含控制字符或不允许字符时抛出。
        """

        if value is not None and _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("调用关联字段仅允许字母、数字、点、下划线、冒号和连字符")
        return value

    @field_validator("generation_params")
    @classmethod
    def _validate_generation_params(cls, value: JsonMap) -> JsonMap:
        """拒绝覆盖 LlmGateway 管理的保留请求字段。

        :param value: 原始生成参数映射。
        :return: 未包含保留字段的生成参数映射。
        :raises ValueError: 当生成参数试图覆盖模型、消息、工具或流式字段时抛出。
        """

        reserved_fields = sorted(_RESERVED_GENERATION_PARAM_NAMES.intersection(value))
        if reserved_fields:
            raise ValueError(
                "generation_params 不得覆盖保留字段: " + ", ".join(reserved_fields)
            )
        return value

    @model_validator(mode="after")
    def _validate_stream_flag(self) -> "LlmInvocationRequestDto":
        """校验流式请求入口与请求标记一致。

        :return: 当前调用请求。
        """

        return self


class LlmInvocationResultDto(LlmGatewayDto):
    """一次成功的非流式模型调用结果。"""

    call_id: str = Field(
        min_length=1,
        description="逻辑模型调用 ID。",
    )
    model_profile_id: str = Field(
        min_length=1,
        description="调用方请求的根模型 profile ID。",
    )
    actual_profile_id: str = Field(
        min_length=1,
        description="最终成功执行的模型 profile ID。",
    )
    provider_route_id: str = Field(
        min_length=1,
        description="最终成功执行的供应商路由 ID。",
    )
    actual_model: str = Field(
        min_length=1,
        description="供应商或模型代理返回的实际模型标识。",
    )
    content: str | None = Field(
        default=None,
        description="归一化模型文本结果。",
    )
    tool_calls: list[LlmToolCallDto] = Field(
        default_factory=list,
        description="归一化模型工具调用。",
    )
    finish_reason: LlmFinishReason = Field(
        description="归一化模型完成原因。",
    )
    usage: LlmUsageSummaryDto = Field(
        default_factory=LlmUsageSummaryDto,
        description="模型 token 使用摘要。",
    )
    latency_ms: int = Field(
        ge=0,
        description="逻辑模型调用总耗时。",
    )
    retry_count: int = Field(
        ge=0,
        description="除首次物理调用外的重试次数。",
    )
    fallback_used: bool = Field(
        description="是否使用了备用模型 profile。",
    )
    fallback_chain: list[str] = Field(
        default_factory=list,
        description="实际尝试过的模型 profile 顺序。",
    )
    trace_write_status: LlmTraceWriteStatus = Field(
        default=LlmTraceWriteStatus.SKIPPED,
        description="模型调用摘要写入状态。",
    )
    normalized_error: LlmErrorDto | None = Field(
        default=None,
        description="成功结果固定为空；字段用于保持稳定返回契约。",
    )


class LlmStreamEventDto(LlmGatewayDto):
    """一次流式模型调用的归一化事件。"""

    call_id: str = Field(
        min_length=1,
        description="逻辑模型调用 ID。",
    )
    event_type: LlmStreamEventType = Field(
        description="流式事件类型。",
    )
    model_profile_id: str = Field(
        min_length=1,
        description="调用方请求的根模型 profile ID。",
    )
    actual_profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前实际执行的模型 profile ID。",
    )
    provider_route_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前实际执行的供应商路由 ID。",
    )
    actual_model: str | None = Field(
        default=None,
        min_length=1,
        description="供应商返回的实际模型标识。",
    )
    delta: str = Field(
        default="",
        description="文本增量。",
    )
    tool_call_deltas: list[LlmToolCallDeltaDto] = Field(
        default_factory=list,
        description="工具调用增量。",
    )
    finish_reason: LlmFinishReason | None = Field(
        default=None,
        description="完成事件携带的归一化完成原因。",
    )
    usage: LlmUsageSummaryDto | None = Field(
        default=None,
        description="usage 或完成事件携带的 token 使用摘要。",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="首个用户可见事件产生前发生的重试次数。",
    )
    fallback_chain: list[str] = Field(
        default_factory=list,
        description="实际尝试过的模型 profile 顺序。",
    )
    latency_ms: int | None = Field(
        default=None,
        ge=0,
        description="开始或完成事件携带的逻辑调用耗时。",
    )
    first_token_latency_ms: int | None = Field(
        default=None,
        ge=0,
        description="首个有效模型事件延迟。",
    )
    trace_write_status: LlmTraceWriteStatus | None = Field(
        default=None,
        description="完成或错误事件携带的模型调用摘要写入状态。",
    )
    normalized_error: LlmErrorDto | None = Field(
        default=None,
        description="错误事件携带的标准错误。",
    )


class LlmTokenEstimateDto(LlmGatewayDto):
    """调用前 token 预算估算结果。"""

    model_profile_id: str = Field(
        min_length=1,
        description="执行估算的模型 profile ID。",
    )
    provider_route_id: str = Field(
        min_length=1,
        description="执行上下文检查的供应商路由 ID。",
    )
    input_tokens: int = Field(
        ge=0,
        description="估算输入 token 数。",
    )
    reserved_output_tokens: int = Field(
        ge=1,
        description="为模型输出预留的 token 数。",
    )
    total_budget_tokens: int = Field(
        ge=1,
        description="输入估算与输出预留之和。",
    )
    max_context_tokens: int = Field(
        ge=1,
        description="目标路由声明的最大上下文长度。",
    )
    estimated: bool = Field(
        default=True,
        description="当前预算是否为本地估算。",
    )


class ProviderInvocationRequestDto(LlmGatewayDto):
    """传递给 ProviderAdapter 的归一化物理调用请求。"""

    call_id: str = Field(
        min_length=1,
        description="逻辑模型调用 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="全链路追踪 ID。",
    )
    request_id: str = Field(
        min_length=1,
        description="入口请求 ID。",
    )
    caller_component: str = Field(
        min_length=1,
        description="调用组件名。",
    )
    model_alias: str = Field(
        min_length=1,
        description="发送给代理的模型别名。",
    )
    messages: list[LlmMessageDto] = Field(
        min_length=1,
        description="模型消息。",
    )
    response_format: LlmResponseFormatDto = Field(
        description="模型响应格式。",
    )
    tool_schemas: list[LlmToolSchemaDto] = Field(
        default_factory=list,
        description="授权工具 schema。",
    )
    stream: bool = Field(
        description="是否执行流式物理调用。",
    )
    generation_params: JsonMap = Field(
        default_factory=dict,
        description="生成参数。",
    )


class ProviderInvocationResponseDto(LlmGatewayDto):
    """ProviderAdapter 非流式归一化响应。"""

    actual_model: str = Field(
        min_length=1,
        description="供应商返回的实际模型标识。",
    )
    content: str | None = Field(
        default=None,
        description="模型文本结果。",
    )
    tool_calls: list[LlmToolCallDto] = Field(
        default_factory=list,
        description="模型工具调用。",
    )
    finish_reason: LlmFinishReason = Field(
        description="归一化完成原因。",
    )
    usage: LlmUsageSummaryDto = Field(
        default_factory=LlmUsageSummaryDto,
        description="供应商返回的 token 使用摘要。",
    )


class ProviderStreamEventDto(LlmGatewayDto):
    """ProviderAdapter 流式归一化事件。"""

    event_type: ProviderStreamEventType = Field(
        description="ProviderAdapter 内部流式事件类型。",
    )
    actual_model: str | None = Field(
        default=None,
        min_length=1,
        description="供应商返回的实际模型标识。",
    )
    delta: str = Field(
        default="",
        description="文本增量。",
    )
    tool_call_deltas: list[LlmToolCallDeltaDto] = Field(
        default_factory=list,
        description="工具调用增量。",
    )
    finish_reason: LlmFinishReason | None = Field(
        default=None,
        description="完成事件携带的完成原因。",
    )
    usage: LlmUsageSummaryDto | None = Field(
        default=None,
        description="usage 或完成事件携带的 token 使用摘要。",
    )


class LlmProviderRouteHealthDto(LlmGatewayDto):
    """供应商路由健康检查结果。"""

    provider_route_id: str = Field(
        min_length=1,
        description="供应商路由 ID。",
    )
    healthy: bool = Field(
        description="路由是否可用。",
    )
    latency_ms: int | None = Field(
        default=None,
        ge=0,
        description="健康检查耗时。",
    )
    status_code: int | None = Field(
        default=None,
        ge=100,
        le=599,
        description="代理健康端点返回的 HTTP 状态码。",
    )
    reason: str | None = Field(
        default=None,
        max_length=256,
        description="不含响应正文的健康检查失败原因。",
    )


class LlmModelProfileStatusDto(LlmGatewayDto):
    """模型 profile 静态可用性检查结果。"""

    model_profile_id: str = Field(
        min_length=1,
        description="模型 profile ID。",
    )
    profile_version: str = Field(
        min_length=1,
        description="模型 profile 版本。",
    )
    provider_route_id: str = Field(
        min_length=1,
        description="profile 引用的供应商路由 ID。",
    )
    available: bool = Field(
        description="profile 配置和适配器是否具备执行条件。",
    )
    reason: str | None = Field(
        default=None,
        max_length=256,
        description="profile 不可用原因。",
    )


class LlmCallSummaryDto(LlmGatewayDto):
    """可提交给 LogicTraceStore 的脱敏模型调用摘要。"""

    call_id: str = Field(
        min_length=1,
        description="逻辑模型调用 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="全链路追踪 ID。",
    )
    request_id: str = Field(
        min_length=1,
        description="入口请求 ID。",
    )
    caller_component: str = Field(
        min_length=1,
        description="调用组件名。",
    )
    requested_profile_id: str = Field(
        min_length=1,
        description="调用方请求的根模型 profile ID。",
    )
    actual_profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="最终执行的模型 profile ID。",
    )
    provider_route_id: str | None = Field(
        default=None,
        min_length=1,
        description="最终执行的供应商路由 ID。",
    )
    actual_model: str | None = Field(
        default=None,
        min_length=1,
        description="供应商返回的实际模型标识。",
    )
    status: Literal["succeeded", "failed", "cancelled"] = Field(
        description="模型调用最终状态。",
    )
    finish_reason: LlmFinishReason | None = Field(
        default=None,
        description="成功调用的完成原因。",
    )
    usage: LlmUsageSummaryDto = Field(
        default_factory=LlmUsageSummaryDto,
        description="模型 token 使用摘要。",
    )
    latency_ms: int = Field(
        ge=0,
        description="逻辑模型调用总耗时。",
    )
    first_token_latency_ms: int | None = Field(
        default=None,
        ge=0,
        description="流式调用首个有效事件延迟。",
    )
    retry_count: int = Field(
        ge=0,
        description="重试次数。",
    )
    fallback_chain: list[str] = Field(
        default_factory=list,
        description="实际尝试过的模型 profile 顺序。",
    )
    error_code: LlmGatewayErrorCode | None = Field(
        default=None,
        description="失败调用的标准错误码。",
    )
    config_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        description="调用绑定的 RuntimeConfig 快照 ID。",
    )


class LlmTraceWriteResultDto(LlmGatewayDto):
    """模型调用摘要写入结果。"""

    status: LlmTraceWriteStatus = Field(
        description="摘要写入状态。",
    )
    reason: str | None = Field(
        default=None,
        max_length=256,
        description="摘要写入降级或跳过原因。",
    )


__all__: tuple[str, ...] = (
    "JsonMap",
    "LlmCallSummaryDto",
    "LlmContentPartDto",
    "LlmErrorDto",
    "LlmFunctionCallDto",
    "LlmGatewayDto",
    "LlmImageContentPartDto",
    "LlmImageUrlDto",
    "LlmInvocationRequestDto",
    "LlmInvocationResultDto",
    "LlmJsonSchemaDto",
    "LlmMessageDto",
    "LlmModelProfileStatusDto",
    "LlmProviderRouteHealthDto",
    "LlmResponseFormatDto",
    "LlmStreamEventDto",
    "LlmTextContentPartDto",
    "LlmTokenEstimateDto",
    "LlmToolCallDeltaDto",
    "LlmToolCallDto",
    "LlmToolFunctionSchemaDto",
    "LlmToolSchemaDto",
    "LlmTraceWriteResultDto",
    "LlmUsageSummaryDto",
    "ProviderInvocationRequestDto",
    "ProviderInvocationResponseDto",
    "ProviderStreamEventDto",
)
