##################################################################################################
# 文件: src/veterinary_agent/config/api_ingress.py
# 作用: 定义 API 接入组件的配置化数据模型，并通过 Pydantic Settings 原生 YAML 源加载配置。
# 边界: 仅描述 ApiIngress 的运行参数，不包含外部 API 固定 DTO、业务路由、安全判决或 L1/L2 组件策略。
##################################################################################################

from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_API_INGRESS_CONFIG_PATH = Path("configs/api_ingress.yaml")


def _default_allowed_mime_types() -> list[str]:
    """生成默认允许的附件 MIME 类型列表。

    :return: 默认允许的附件 MIME 类型列表。
    """

    return [
        "image/jpeg",
        "image/png",
        "image/webp",
    ]


class _StrictConfigModel(BaseModel):
    """配置模型基类。

    该基类统一开启额外字段拒绝与赋值校验，避免 YAML 中的拼写错误被静默吞掉。
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串配置值。

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class RequestIdentityConfig(_StrictConfigModel):
    """请求 ID 与链路 ID 配置。"""

    request_id_header: str = Field(
        default="X-Request-ID",
        min_length=1,
        max_length=128,
        description="上游请求 ID 的 HTTP Header 名称。",
    )
    trace_id_header: str = Field(
        default="X-Trace-ID",
        min_length=1,
        max_length=128,
        description="上游链路 ID 的 HTTP Header 名称。",
    )
    allow_body_ids: bool = Field(
        default=True,
        description="是否允许从请求体读取 request_id 与 trace_id。",
    )
    generate_when_missing: bool = Field(
        default=True,
        description="当请求头与请求体均未提供 ID 时，是否由入口层生成。",
    )
    request_id_prefix: str = Field(
        default="req_",
        min_length=1,
        max_length=32,
        description="入口层生成 request_id 时使用的前缀。",
    )
    trace_id_prefix: str = Field(
        default="trace_",
        min_length=1,
        max_length=32,
        description="入口层生成 trace_id 时使用的前缀。",
    )
    max_id_length: int = Field(
        default=128,
        ge=16,
        le=256,
        description="request_id 与 trace_id 允许的最大长度。",
    )
    allowed_id_pattern: str = Field(
        default=r"^[A-Za-z0-9_\-:.]+$",
        min_length=1,
        description="request_id 与 trace_id 的合法字符正则表达式。",
    )


class RequestLimitConfig(_StrictConfigModel):
    """请求体入口限制配置。"""

    max_body_bytes: int = Field(
        default=1_048_576,
        ge=1,
        description="单次 JSON 请求体允许的最大字节数。",
    )
    max_metadata_bytes: int = Field(
        default=16_384,
        ge=0,
        description="顶层 metadata 允许的最大序列化字节数。",
    )
    max_input_items: int = Field(
        default=20,
        ge=1,
        description="input 数组允许的最大条目数。",
    )
    max_content_items_per_message: int = Field(
        default=20,
        ge=1,
        description="单条 message 中 content 数组允许的最大条目数。",
    )
    max_text_chars_per_item: int = Field(
        default=10_000,
        ge=1,
        description="单个 input_text 条目允许的最大字符数。",
    )
    max_total_text_chars: int = Field(
        default=40_000,
        ge=1,
        description="单轮请求所有文本内容合计允许的最大字符数。",
    )
    allow_empty_input_when_attachments_present: bool = Field(
        default=True,
        description="当 attachments 有有效内容时，是否允许 input 为空。",
    )
    allow_attachment_only_turn: bool = Field(
        default=True,
        description="是否允许仅携带附件元信息而不携带文本输入。",
    )
    parse_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="入口层解析请求体的超时时间，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_text_total_limit(self) -> Self:
        """校验单轮文本总量限制。

        :return: 通过校验后的当前配置对象。
        :raises ValueError: 当单轮文本总量小于单项文本限制时抛出。
        """

        if self.max_total_text_chars < self.max_text_chars_per_item:
            raise ValueError("max_total_text_chars 不得小于 max_text_chars_per_item")
        return self


class AttachmentLimitConfig(_StrictConfigModel):
    """附件元信息入口限制配置。"""

    max_attachments: int = Field(
        default=10,
        ge=0,
        description="单轮请求允许的最大附件数量。",
    )
    max_attachment_metadata_bytes: int = Field(
        default=8_192,
        ge=0,
        description="单个附件 metadata 允许的最大序列化字节数。",
    )
    max_total_attachment_metadata_bytes: int = Field(
        default=32_768,
        ge=0,
        description="单轮请求所有附件元信息合计允许的最大序列化字节数。",
    )
    allowed_mime_types: list[str] = Field(
        default_factory=_default_allowed_mime_types,
        description="入口层允许接收的附件 MIME 类型列表。",
    )
    allow_unknown_mime_type: bool = Field(
        default=False,
        description="是否允许未出现在 allowed_mime_types 中的 MIME 类型。",
    )
    max_storage_ref_length: int = Field(
        default=1024,
        ge=1,
        description="storage_ref 字段允许的最大字符数。",
    )
    max_attachment_id_length: int = Field(
        default=128,
        ge=1,
        description="attachment_id 字段允许的最大字符数。",
    )
    max_purpose_length: int = Field(
        default=64,
        ge=1,
        description="purpose 字段允许的最大字符数。",
    )

    @field_validator("allowed_mime_types")
    @classmethod
    def _validate_mime_types(cls, values: list[str]) -> list[str]:
        """校验并规范化 MIME 类型列表。

        :param values: YAML 或环境变量传入的 MIME 类型列表。
        :return: 去重且去除空白后的 MIME 类型列表。
        :raises ValueError: 当列表中存在空字符串时抛出。
        """

        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("allowed_mime_types 不得包含空字符串")
        return list(dict.fromkeys(normalized_values))


class ResponseModeConfig(_StrictConfigModel):
    """同步与流式响应模式配置。"""

    default_stream: bool = Field(
        default=False,
        description="当请求未传 stream 字段时，是否默认采用 SSE 流式响应。",
    )
    allow_sync: bool = Field(
        default=True,
        description="是否允许同步 JSON 响应。",
    )
    allow_stream: bool = Field(
        default=True,
        description="是否允许 SSE 流式响应。",
    )
    sync_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="同步响应等待编排层完成的最大时间，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_allowed_response_modes(self) -> Self:
        """校验至少存在一种可用响应模式。

        :return: 通过校验后的当前配置对象。
        :raises ValueError: 当同步与流式响应均被禁用时抛出。
        """

        if not self.allow_sync and not self.allow_stream:
            raise ValueError("allow_sync 与 allow_stream 不得同时为 false")
        return self


class SseConfig(_StrictConfigModel):
    """SSE 流式连接配置。"""

    heartbeat_enabled: bool = Field(
        default=True,
        description="是否启用入口层 SSE 心跳事件。",
    )
    heartbeat_interval_seconds: float = Field(
        default=15.0,
        gt=0,
        description="SSE 心跳事件发送间隔，单位为秒。",
    )
    idle_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="流式连接无业务事件或心跳时的空闲超时，单位为秒。",
    )
    first_event_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description="等待编排层首个流式事件的最大时间，单位为秒。",
    )
    max_stream_duration_seconds: float = Field(
        default=300.0,
        gt=0,
        description="单次流式连接允许的最大持续时间，单位为秒。",
    )
    max_event_bytes: int = Field(
        default=65_536,
        ge=1,
        description="单个 SSE data 事件允许的最大序列化字节数。",
    )
    client_cancel_notify_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description="客户端断开后通知编排层取消的等待时间，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_stream_timeouts(self) -> Self:
        """校验流式连接相关超时的相对关系。

        :return: 通过校验后的当前配置对象。
        :raises ValueError: 当首事件超时或心跳间隔不合理时抛出。
        """

        if self.first_event_timeout_seconds > self.max_stream_duration_seconds:
            raise ValueError(
                "first_event_timeout_seconds 不得大于 max_stream_duration_seconds"
            )
        if (
            self.heartbeat_enabled
            and self.heartbeat_interval_seconds >= self.idle_timeout_seconds
        ):
            raise ValueError("heartbeat_interval_seconds 必须小于 idle_timeout_seconds")
        return self


class OrchestratorClientConfig(_StrictConfigModel):
    """编排层调用配置。"""

    target: str = Field(
        default="local",
        min_length=1,
        description="编排入口目标，可为本地适配器标识或远端服务地址。",
    )
    connect_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        description="连接编排入口的超时时间，单位为秒。",
    )
    request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="同步调用编排层的请求超时时间，单位为秒。",
    )
    stream_first_event_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description="等待编排层流式首事件的超时时间，单位为秒。",
    )
    stream_total_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="编排层流式调用允许的最大总时长，单位为秒。",
    )
    max_concurrency: int = Field(
        default=100,
        ge=1,
        description="入口层允许同时转发给编排层的最大请求数。",
    )

    @model_validator(mode="after")
    def _validate_orchestrator_timeouts(self) -> Self:
        """校验编排层调用超时的相对关系。

        :return: 通过校验后的当前配置对象。
        :raises ValueError: 当连接超时或首事件超时超过总超时时抛出。
        """

        if self.connect_timeout_seconds >= self.request_timeout_seconds:
            raise ValueError("connect_timeout_seconds 必须小于 request_timeout_seconds")
        if self.stream_first_event_timeout_seconds > self.stream_total_timeout_seconds:
            raise ValueError(
                "stream_first_event_timeout_seconds 不得大于 stream_total_timeout_seconds"
            )
        return self


class RateLimitConfig(_StrictConfigModel):
    """入口限流配置。"""

    enabled: bool = Field(
        default=False,
        description="是否启用入口层限流。",
    )
    max_requests_per_minute: int = Field(
        default=600,
        ge=1,
        description="实例级每分钟最大请求数。",
    )
    max_active_streams: int = Field(
        default=100,
        ge=1,
        description="实例级最大活跃 SSE 连接数。",
    )
    per_path_enabled: bool = Field(
        default=True,
        description="限流启用时是否按请求路径区分计数。",
    )
    per_client_source_enabled: bool = Field(
        default=False,
        description="限流启用时是否按客户端来源区分计数。",
    )


class ErrorResponseConfig(_StrictConfigModel):
    """错误响应行为配置。"""

    include_details: bool = Field(
        default=True,
        description="错误响应中是否包含 details 字段。",
    )
    max_details: int = Field(
        default=20,
        ge=0,
        description="错误响应 details 数组允许的最大条目数。",
    )
    detailed_message_enabled: bool = Field(
        default=False,
        description="是否在错误 message 中暴露更详细的研发诊断信息。",
    )
    hide_internal_dependency_details: bool = Field(
        default=True,
        description="是否隐藏内部依赖异常的具体细节。",
    )
    default_message: str = Field(
        default="request failed",
        min_length=1,
        max_length=256,
        description="未知异常映射为统一错误时使用的默认消息。",
    )


class ReadinessConfig(_StrictConfigModel):
    """服务就绪检查配置。"""

    check_runtime_config: bool = Field(
        default=True,
        description="/ready 是否检查 RuntimeConfig 可用性。",
    )
    check_orchestrator: bool = Field(
        default=True,
        description="/ready 是否检查编排入口可用性。",
    )
    validate_required_parameters: bool = Field(
        default=True,
        description="/ready 是否校验入口必要参数完整性。",
    )
    allow_degraded_observability: bool = Field(
        default=True,
        description="Observability 降级时是否仍允许服务就绪。",
    )
    orchestrator_check_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description="就绪检查中编排入口探测的超时时间，单位为秒。",
    )


class OpenAICompatibilityConfig(_StrictConfigModel):
    """OpenAI Responses 风格兼容入口配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用 /openai/v1/responses 兼容入口。",
    )


class ApiIngressSettings(BaseSettings):
    """API 接入组件根配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="API_INGRESS_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_API_INGRESS_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(
        default=True,
        description="是否启用 API 接入组件。",
    )
    service_name: str = Field(
        default="veterinary-agent-api-ingress",
        min_length=1,
        max_length=128,
        description="入口组件服务名称。",
    )
    environment: str = Field(
        default="local",
        min_length=1,
        max_length=64,
        description="当前运行环境标识。",
    )
    config_version: str = Field(
        default="api-ingress.v1",
        min_length=1,
        max_length=128,
        description="配置版本，用于日志、排障和后续逻辑链关联。",
    )
    request_identity: RequestIdentityConfig = Field(
        default_factory=RequestIdentityConfig,
        description="请求 ID 与链路 ID 配置。",
    )
    request_limits: RequestLimitConfig = Field(
        default_factory=RequestLimitConfig,
        description="请求体入口限制配置。",
    )
    attachment_limits: AttachmentLimitConfig = Field(
        default_factory=AttachmentLimitConfig,
        description="附件元信息入口限制配置。",
    )
    response_mode: ResponseModeConfig = Field(
        default_factory=ResponseModeConfig,
        description="同步与流式响应模式配置。",
    )
    sse: SseConfig = Field(
        default_factory=SseConfig,
        description="SSE 流式连接配置。",
    )
    orchestrator: OrchestratorClientConfig = Field(
        default_factory=OrchestratorClientConfig,
        description="编排层调用配置。",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="入口限流配置。",
    )
    error_response: ErrorResponseConfig = Field(
        default_factory=ErrorResponseConfig,
        description="错误响应行为配置。",
    )
    readiness: ReadinessConfig = Field(
        default_factory=ReadinessConfig,
        description="服务就绪检查配置。",
    )
    openai_compatibility: OpenAICompatibilityConfig = Field(
        default_factory=OpenAICompatibilityConfig,
        description="OpenAI Responses 风格兼容入口配置。",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_root_string_value(cls, value: Any) -> Any:
        """清理根配置中的字符串值。

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定制 Pydantic Settings 的配置来源顺序。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置来源。
        :param env_settings: 环境变量配置来源。
        :param dotenv_settings: .env 文件配置来源。
        :param file_secret_settings: 文件密钥配置来源。
        :return: 按优先级排列后的配置来源元组。
        """

        if cls.yaml_config_path is None:
            yaml_source = YamlConfigSettingsSource(settings_cls)
        else:
            yaml_source = YamlConfigSettingsSource(
                settings_cls, yaml_file=cls.yaml_config_path
            )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


def load_api_ingress_settings(
    config_path: str | Path | None = None,
) -> ApiIngressSettings:
    """加载 API 接入组件配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用模型默认路径。
    :return: 已完成校验的 API 接入组件配置对象。
    """

    if config_path is None:
        return ApiIngressSettings()

    class _PathBoundApiIngressSettings(ApiIngressSettings):
        """绑定指定 YAML 文件路径的 API 接入配置类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundApiIngressSettings()


__all__: tuple[str, ...] = (
    "ApiIngressSettings",
    "AttachmentLimitConfig",
    "ErrorResponseConfig",
    "OpenAICompatibilityConfig",
    "OrchestratorClientConfig",
    "RateLimitConfig",
    "ReadinessConfig",
    "RequestIdentityConfig",
    "RequestLimitConfig",
    "ResponseModeConfig",
    "SseConfig",
    "load_api_ingress_settings",
)
