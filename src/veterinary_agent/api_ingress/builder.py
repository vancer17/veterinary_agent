##################################################################################################
# 文件: src/veterinary_agent/api_ingress/builder.py
# 作用: 定义 AgentTurnRequest Builder，将 ApiIngress 内部归一化请求转换为编排层可消费的请求命令。
# 边界: 仅执行字段映射、幂等键补齐、执行选项与发布能力封装；不调用编排层、不访问存储、不执行兽医业务判断。
##################################################################################################

from pydantic import Field

from veterinary_agent.api_ingress.dto import (
    AgentTurnInternalRequestDto,
    ApiIngressDto,
    AttachmentRefDto,
    InputItemDto,
    JsonMap,
    RequestContextDto,
    TrustedIdentityDto,
    TurnOptionsDto,
)
from veterinary_agent.api_ingress.enums import ResponseMode
from veterinary_agent.config import ApiIngressSettings


class AgentTurnExecutionOptionsDto(ApiIngressDto):
    """一轮 Agent 编排执行选项 DTO。"""

    orchestrator_target: str = Field(
        min_length=1,
        description="编排入口目标标识，可为本地适配器或远端服务地址。",
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
        description="等待编排层流式首事件的超时时间，单位为秒。",
    )
    stream_total_timeout_seconds: float = Field(
        gt=0,
        description="编排层流式调用允许的最大总时长，单位为秒。",
    )
    heartbeat_enabled: bool = Field(
        description="是否允许入口层在 SSE 模式发送心跳事件。",
    )
    heartbeat_interval_seconds: float = Field(
        gt=0,
        description="SSE 心跳事件发送间隔，单位为秒。",
    )
    stream_idle_timeout_seconds: float = Field(
        gt=0,
        description="SSE 流式连接空闲超时时间，单位为秒。",
    )
    max_stream_duration_seconds: float = Field(
        gt=0,
        description="单次 SSE 流式连接允许的最大持续时间，单位为秒。",
    )
    max_event_bytes: int = Field(
        ge=1,
        description="单个 SSE 事件允许的最大序列化字节数。",
    )
    client_cancel_notify_timeout_seconds: float = Field(
        gt=0,
        description="客户端断开后通知下游取消的等待时间，单位为秒。",
    )


class AgentTurnPublishCapabilitiesDto(ApiIngressDto):
    """ApiIngress 对本轮请求可承载的发布能力 DTO。"""

    supports_segments: bool = Field(
        default=True,
        description="当前入口是否支持承载业务分段输出。",
    )
    supports_reasoning_display: bool = Field(
        default=True,
        description="当前入口是否支持承载可展示 reasoning display。",
    )
    supports_sse_events: bool = Field(
        description="当前请求是否支持 SSE 事件流式发布。",
    )


class AgentTurnDiagnosticsDto(ApiIngressDto):
    """一轮 Agent 请求的入口诊断摘要 DTO。"""

    service_name: str = Field(
        min_length=1,
        description="入口组件服务名称。",
    )
    environment: str = Field(
        min_length=1,
        description="当前运行环境标识。",
    )
    config_version: str = Field(
        min_length=1,
        description="入口组件配置版本。",
    )
    input_count: int = Field(
        ge=0,
        description="归一化后的输入项数量。",
    )
    attachment_count: int = Field(
        ge=0,
        description="归一化后的附件数量。",
    )


class AgentTurnRequestCommandDto(ApiIngressDto):
    """面向 VetOrchestrator / GraphRuntime 的一轮 Agent 请求命令 DTO。"""

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
        description="归一化后的客户端普通透传元信息。",
    )
    model_hint: str | None = Field(
        default=None,
        min_length=1,
        description="客户端传入的模型或模型策略提示；最终模型选择由下游决定。",
    )
    turn_options: TurnOptionsDto | None = Field(
        default=None,
        description="本轮入口选项。",
    )
    idempotency_key: str = Field(
        min_length=1,
        description="传递给编排层或持久化层的幂等键。",
    )
    execution_options: AgentTurnExecutionOptionsDto = Field(
        description="编排执行选项。",
    )
    publish_capabilities: AgentTurnPublishCapabilitiesDto = Field(
        description="当前入口可承载的发布能力。",
    )
    diagnostics: AgentTurnDiagnosticsDto = Field(
        description="入口诊断摘要。",
    )


def _resolve_idempotency_key(
    normalized_request: AgentTurnInternalRequestDto,
) -> str:
    """解析传递给下游的幂等键。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :return: 下游可使用的幂等键；未显式传入时回退为 request_id。
    """

    turn_options = normalized_request.turn_options
    if turn_options and turn_options.idempotency_key:
        return turn_options.idempotency_key
    return normalized_request.request_context.request_id


def _build_execution_options(
    settings: ApiIngressSettings,
) -> AgentTurnExecutionOptionsDto:
    """构建编排执行选项。

    :param settings: API 接入组件配置。
    :return: 编排层可消费的一轮执行选项 DTO。
    """

    return AgentTurnExecutionOptionsDto(
        orchestrator_target=settings.orchestrator.target,
        connect_timeout_seconds=settings.orchestrator.connect_timeout_seconds,
        request_timeout_seconds=settings.orchestrator.request_timeout_seconds,
        stream_first_event_timeout_seconds=(
            settings.orchestrator.stream_first_event_timeout_seconds
        ),
        stream_total_timeout_seconds=settings.orchestrator.stream_total_timeout_seconds,
        heartbeat_enabled=settings.sse.heartbeat_enabled,
        heartbeat_interval_seconds=settings.sse.heartbeat_interval_seconds,
        stream_idle_timeout_seconds=settings.sse.idle_timeout_seconds,
        max_stream_duration_seconds=settings.sse.max_stream_duration_seconds,
        max_event_bytes=settings.sse.max_event_bytes,
        client_cancel_notify_timeout_seconds=(
            settings.sse.client_cancel_notify_timeout_seconds
        ),
    )


def _build_publish_capabilities(
    normalized_request: AgentTurnInternalRequestDto,
) -> AgentTurnPublishCapabilitiesDto:
    """构建当前入口可承载的发布能力。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :return: 当前请求对应的发布能力 DTO。
    """

    return AgentTurnPublishCapabilitiesDto(
        supports_sse_events=(
            normalized_request.request_context.response_mode is ResponseMode.STREAM
        ),
    )


def _build_diagnostics(
    normalized_request: AgentTurnInternalRequestDto,
    settings: ApiIngressSettings,
) -> AgentTurnDiagnosticsDto:
    """构建入口诊断摘要。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 当前一轮请求的入口诊断摘要 DTO。
    """

    return AgentTurnDiagnosticsDto(
        service_name=settings.service_name,
        environment=settings.environment,
        config_version=settings.config_version,
        input_count=len(normalized_request.input),
        attachment_count=len(normalized_request.attachments),
    )


def build_agent_turn_request(
    normalized_request: AgentTurnInternalRequestDto,
    settings: ApiIngressSettings,
) -> AgentTurnRequestCommandDto:
    """构建面向编排层的一轮 Agent 请求命令。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 可传递给后续 TODO 编排适配器或真实编排层的请求命令 DTO。
    """

    return AgentTurnRequestCommandDto(
        request_context=normalized_request.request_context,
        trusted_identity=normalized_request.trusted_identity,
        input=list(normalized_request.input),
        attachments=list(normalized_request.attachments),
        metadata=dict(normalized_request.metadata),
        model_hint=normalized_request.model,
        turn_options=normalized_request.turn_options,
        idempotency_key=_resolve_idempotency_key(normalized_request),
        execution_options=_build_execution_options(settings),
        publish_capabilities=_build_publish_capabilities(normalized_request),
        diagnostics=_build_diagnostics(normalized_request, settings),
    )


__all__: tuple[str, ...] = (
    "AgentTurnDiagnosticsDto",
    "AgentTurnExecutionOptionsDto",
    "AgentTurnPublishCapabilitiesDto",
    "AgentTurnRequestCommandDto",
    "build_agent_turn_request",
)
