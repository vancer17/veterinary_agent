##################################################################################################
# 文件: src/veterinary_agent/config/observability.py
# 作用: 定义 Observability 组件 RuntimeConfig 配置模型，并通过 Pydantic Settings 加载 YAML/env。
# 边界: 仅描述可观测性运行参数；不初始化 exporter、不注册 FastAPI 路由、不执行具体打点逻辑。
##################################################################################################

import re
from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_OBSERVABILITY_CONFIG_PATH = Path("configs/observability.yaml")

_METRIC_LABEL_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _default_allowed_metric_labels() -> list[str]:
    """生成默认允许进入指标系统的低基数 label 名称。

    :return: 默认低基数指标 label 名称列表。
    """

    return [
        "agent_name",
        "component",
        "endpoint",
        "error_type",
        "exporter_type",
        "fallback_reason_code",
        "generation_profile",
        "graph_name",
        "is_first_segment",
        "method",
        "model_name",
        "model_provider",
        "node_name",
        "rag_mode",
        "segment_type",
        "stage",
        "status",
        "status_code",
        "streaming",
        "tool_name",
    ]


def _default_forbidden_metric_labels() -> list[str]:
    """生成默认禁止进入指标系统的高基数或敏感 label 名称。

    :return: 默认禁止指标 label 名称列表。
    """

    return [
        "attachment_id",
        "message_id",
        "model_call_id",
        "node_run_id",
        "pet_id",
        "request_id",
        "run_id",
        "segment_id",
        "session_id",
        "span_id",
        "task_id",
        "tool_call_id",
        "trace_id",
        "user_id",
    ]


def _default_duration_buckets_seconds() -> list[float]:
    """生成默认 Prometheus histogram 耗时桶。

    :return: 默认耗时桶边界，单位为秒。
    """

    return [
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
        60.0,
    ]


class _StrictConfigModel(BaseModel):
    """Observability 配置模型基类。"""

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


class ObservabilityMetricsConfig(_StrictConfigModel):
    """Observability metrics 配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用应用内 metrics 聚合。",
    )
    endpoint_enabled: bool = Field(
        default=True,
        description="是否暴露 Prometheus 文本格式 metrics endpoint。",
    )
    endpoint_path: str = Field(
        default="/metrics",
        min_length=1,
        max_length=128,
        description="Prometheus 抓取指标端点路径。",
    )
    exclude_paths: list[str] = Field(
        default_factory=lambda: ["/metrics", "/health", "/ready"],
        description="不纳入 HTTP 请求指标统计的路径列表。",
    )
    duration_buckets_seconds: list[float] = Field(
        default_factory=_default_duration_buckets_seconds,
        min_length=1,
        description="请求、span 与调用耗时 histogram 桶边界，单位为秒。",
    )
    max_label_value_length: int = Field(
        default=128,
        ge=1,
        le=512,
        description="指标 label 值允许的最大字符数，超过后裁剪。",
    )

    @field_validator("endpoint_path")
    @classmethod
    def _validate_endpoint_path(cls, value: str) -> str:
        """校验 metrics endpoint 路径格式。

        :param value: 待校验的 endpoint 路径。
        :return: 通过校验的 endpoint 路径。
        :raises ValueError: 当 endpoint 路径不是绝对路径时抛出。
        """

        if not value.startswith("/"):
            raise ValueError("endpoint_path 必须以 / 开头")
        return value

    @field_validator("exclude_paths")
    @classmethod
    def _validate_exclude_paths(cls, values: list[str]) -> list[str]:
        """校验并规范化排除路径列表。

        :param values: YAML 或环境变量传入的排除路径列表。
        :return: 去重且保序的排除路径列表。
        :raises ValueError: 当路径为空或不是绝对路径时抛出。
        """

        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("exclude_paths 不得包含空字符串")
        if any(not value.startswith("/") for value in normalized_values):
            raise ValueError("exclude_paths 中的路径必须以 / 开头")
        return list(dict.fromkeys(normalized_values))

    @field_validator("duration_buckets_seconds")
    @classmethod
    def _validate_duration_buckets(cls, values: list[float]) -> list[float]:
        """校验 histogram 耗时桶边界。

        :param values: YAML 或环境变量传入的耗时桶边界。
        :return: 严格递增且大于零的耗时桶边界。
        :raises ValueError: 当桶边界非正数、重复或不是严格递增时抛出。
        """

        if any(value <= 0 for value in values):
            raise ValueError("duration_buckets_seconds 必须全部大于 0")
        if values != sorted(values):
            raise ValueError("duration_buckets_seconds 必须按升序排列")
        if len(set(values)) != len(values):
            raise ValueError("duration_buckets_seconds 不得包含重复值")
        return values


class ObservabilityLoggingConfig(_StrictConfigModel):
    """Observability 结构化日志配置。"""

    enabled: bool = Field(
        default=True,
        description="是否输出 Observability 结构化运行日志。",
    )
    level: str = Field(
        default="INFO",
        min_length=1,
        max_length=16,
        description="Observability logger 日志级别。",
    )
    max_field_bytes: int = Field(
        default=4096,
        ge=256,
        le=65536,
        description="单个结构化日志字段序列化后允许的最大字节数。",
    )

    @field_validator("level")
    @classmethod
    def _validate_level(cls, value: str) -> str:
        """校验并规范化日志级别。

        :param value: YAML 或环境变量传入的日志级别。
        :return: 大写后的日志级别。
        :raises ValueError: 当日志级别不在允许集合中时抛出。
        """

        normalized_value = value.upper()
        if normalized_value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("level 必须是 CRITICAL、ERROR、WARNING、INFO 或 DEBUG")
        return normalized_value


class ObservabilityTracingConfig(_StrictConfigModel):
    """Observability OpenTelemetry tracing 配置。"""

    enabled: bool = Field(
        default=False,
        description="是否启用 OpenTelemetry tracing exporter。",
    )
    sample_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="OpenTelemetry trace 采样率，取值范围 0 到 1。",
    )
    service_name: str = Field(
        default="veterinary-agent",
        min_length=1,
        max_length=128,
        description="OpenTelemetry resource 中的 service.name。",
    )
    environment: str = Field(
        default="local",
        min_length=1,
        max_length=64,
        description="OpenTelemetry resource 中的 deployment.environment。",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        max_length=512,
        description="OTLP exporter endpoint；为空时仅保留本地 span 事件摘要。",
    )
    exporter_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30.0,
        description="OpenTelemetry exporter 单次导出超时时间，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_tracing_endpoint(self) -> Self:
        """校验 tracing 开启时的 endpoint 与采样关系。

        :return: 通过校验后的当前 tracing 配置。
        :raises ValueError: 当启用 tracing 但采样率为零时抛出。
        """

        if self.enabled and self.sample_rate <= 0:
            raise ValueError("tracing.enabled=true 时 sample_rate 必须大于 0")
        return self


class ObservabilityLabelPolicyConfig(_StrictConfigModel):
    """Observability 指标标签治理配置。"""

    allow_unlisted_labels: bool = Field(
        default=False,
        description="是否允许白名单外且未禁止的指标 label。",
    )
    allowed_metric_labels: list[str] = Field(
        default_factory=_default_allowed_metric_labels,
        min_length=1,
        description="允许进入指标系统的低基数 label 名称白名单。",
    )
    forbidden_metric_labels: list[str] = Field(
        default_factory=_default_forbidden_metric_labels,
        min_length=1,
        description="禁止进入指标系统的高基数或敏感 label 名称列表。",
    )

    @field_validator("allowed_metric_labels", "forbidden_metric_labels")
    @classmethod
    def _validate_label_names(cls, values: list[str]) -> list[str]:
        """校验并规范化指标 label 名称列表。

        :param values: YAML 或环境变量传入的 label 名称列表。
        :return: 去重且保序的 label 名称列表。
        :raises ValueError: 当 label 名称为空、重复或格式非法时抛出。
        """

        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("指标 label 名称不得为空")
        if len(set(normalized_values)) != len(normalized_values):
            raise ValueError("指标 label 名称不得重复")
        invalid_values = [
            value
            for value in normalized_values
            if _METRIC_LABEL_PATTERN.fullmatch(value) is None
        ]
        if invalid_values:
            raise ValueError("指标 label 名称格式非法")
        return normalized_values

    @model_validator(mode="after")
    def _validate_label_overlap(self) -> Self:
        """校验允许与禁止 label 集合不得重叠。

        :return: 通过校验后的当前 label 策略配置。
        :raises ValueError: 当允许与禁止 label 集合存在交集时抛出。
        """

        overlap = set(self.allowed_metric_labels).intersection(
            set(self.forbidden_metric_labels)
        )
        if overlap:
            raise ValueError(
                "allowed_metric_labels 与 forbidden_metric_labels 不得重叠"
            )
        return self


class ObservabilitySettings(BaseSettings):
    """Observability 组件 RuntimeConfig 根配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="OBSERVABILITY_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_OBSERVABILITY_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(
        default=True,
        description="是否启用 Observability 组件。",
    )
    config_version: str = Field(
        default="observability.v1",
        min_length=1,
        max_length=128,
        description="Observability 组件配置版本。",
    )
    metrics: ObservabilityMetricsConfig = Field(
        default_factory=ObservabilityMetricsConfig,
        description="metrics 聚合与暴露配置。",
    )
    logging: ObservabilityLoggingConfig = Field(
        default_factory=ObservabilityLoggingConfig,
        description="结构化运行日志配置。",
    )
    tracing: ObservabilityTracingConfig = Field(
        default_factory=ObservabilityTracingConfig,
        description="OpenTelemetry tracing 配置。",
    )
    label_policy: ObservabilityLabelPolicyConfig = Field(
        default_factory=ObservabilityLabelPolicyConfig,
        description="指标 label 白名单、黑名单与高基数治理配置。",
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
                settings_cls,
                yaml_file=cls.yaml_config_path,
            )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


def load_observability_settings(
    config_path: str | Path | None = None,
) -> ObservabilitySettings:
    """加载 Observability 组件 RuntimeConfig。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用模型默认路径。
    :return: 已完成校验的 Observability 组件 RuntimeConfig。
    """

    if config_path is None:
        return ObservabilitySettings()

    class _PathBoundObservabilitySettings(ObservabilitySettings):
        """绑定指定 YAML 文件路径的 Observability RuntimeConfig 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundObservabilitySettings()


__all__: tuple[str, ...] = (
    "DEFAULT_OBSERVABILITY_CONFIG_PATH",
    "ObservabilityLabelPolicyConfig",
    "ObservabilityLoggingConfig",
    "ObservabilityMetricsConfig",
    "ObservabilitySettings",
    "ObservabilityTracingConfig",
    "load_observability_settings",
)
