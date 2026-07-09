##################################################################################################
# 文件: src/veterinary_agent/observability/dto.py
# 作用: 定义 Observability 组件 DTO、上下文、技术 span、结构化事件与统一错误对象。
# 边界: 仅承载应用内观测数据结构；不执行日志输出、指标聚合或外部 exporter 调用。
##################################################################################################

from datetime import datetime
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from veterinary_agent.observability.enums import (
    MetricType,
    ObservabilityErrorCode,
    ObservabilityOperation,
    SpanStatus,
    StructuredLogLevel,
)

JsonMap: TypeAlias = dict[str, object]


class _ObservabilityModel(BaseModel):
    """Observability DTO 模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串字段值。

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class ObservabilityErrorDto(_ObservabilityModel):
    """Observability 统一错误 DTO。"""

    code: ObservabilityErrorCode = Field(
        description="Observability 稳定错误码。",
    )
    operation: ObservabilityOperation = Field(
        description="发生错误的 Observability 操作名。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明。",
    )
    retryable: bool = Field(
        description="调用方是否可以稍后重试。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="冲突对象或被拒绝字段摘要。",
    )


def build_observability_error_dto(
    *,
    code: ObservabilityErrorCode,
    operation: ObservabilityOperation,
    message: str,
    retryable: bool,
    conflict_with: JsonMap | None = None,
) -> ObservabilityErrorDto:
    """构建 Observability 统一错误 DTO。

    :param code: Observability 稳定错误码。
    :param operation: 发生错误的 Observability 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param retryable: 调用方是否可以稍后重试。
    :param conflict_with: 冲突对象或被拒绝字段摘要。
    :return: Observability 统一错误 DTO。
    """

    return ObservabilityErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=retryable,
        conflict_with=conflict_with,
    )


class ObservabilityError(Exception):
    """Observability 领域异常。"""

    def __init__(
        self,
        *,
        code: ObservabilityErrorCode,
        operation: ObservabilityOperation,
        message: str,
        retryable: bool,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 Observability 领域异常。

        :param code: Observability 稳定错误码。
        :param operation: 发生错误的 Observability 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param retryable: 调用方是否可以稍后重试。
        :param conflict_with: 冲突对象或被拒绝字段摘要。
        :return: None。
        """

        self.error = build_observability_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> ObservabilityErrorCode:
        """读取 Observability 稳定错误码。

        :return: 当前异常对应的 Observability 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> ObservabilityOperation:
        """读取发生错误的 Observability 操作名。

        :return: 当前异常对应的 Observability 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以稍后重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> ObservabilityErrorDto:
        """转换为 Observability 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


class ObservabilityContext(_ObservabilityModel):
    """单次请求的技术观测上下文。"""

    request_id: str = Field(
        min_length=1,
        description="本次入口请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本系统业务链路关联 ID。",
    )
    graph_run_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选 GraphRuntime 运行 ID。",
    )
    session_id_hash: str | None = Field(
        default=None,
        min_length=1,
        description="可选 session_id 脱敏 hash。",
    )
    config_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        description="本次请求绑定的 RuntimeConfig 快照 ID。",
    )
    params_version: str | None = Field(
        default=None,
        min_length=1,
        description="本次请求绑定的业务运行参数版本。",
    )
    started_at: datetime = Field(
        description="观测上下文创建时间。",
    )
    safe_attributes: JsonMap = Field(
        default_factory=dict,
        description="允许进入日志与 trace 的安全上下文字段。",
    )


class MetricEvent(_ObservabilityModel):
    """可聚合指标事件。"""

    metric_name: str = Field(
        min_length=1,
        description="指标名称。",
    )
    value: float = Field(
        description="指标观测值。",
    )
    metric_type: MetricType = Field(
        description="指标类型。",
    )
    low_cardinality_labels: dict[str, str] = Field(
        default_factory=dict,
        description="低基数指标 label 集合。",
    )


class TechnicalSpan(_ObservabilityModel):
    """技术链路追踪片段。"""

    span_id: str = Field(
        min_length=1,
        description="当前技术 span ID。",
    )
    parent_span_id: str | None = Field(
        default=None,
        min_length=1,
        description="父级技术 span ID。",
    )
    span_name: str = Field(
        min_length=1,
        description="技术 span 名称。",
    )
    component: str = Field(
        min_length=1,
        description="产生 span 的组件名。",
    )
    started_at: datetime = Field(
        description="span 开始时间。",
    )
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="span 持续时间，单位为毫秒。",
    )
    status: SpanStatus = Field(
        default=SpanStatus.STARTED,
        description="span 当前状态。",
    )
    safe_attributes: JsonMap = Field(
        default_factory=dict,
        description="允许进入日志与 trace 的安全 span 属性。",
    )


class StructuredLogEvent(_ObservabilityModel):
    """结构化运行日志事件。"""

    level: StructuredLogLevel = Field(
        description="结构化日志级别。",
    )
    event_name: str = Field(
        min_length=1,
        description="事件名称。",
    )
    component: str = Field(
        min_length=1,
        description="产生事件的组件名。",
    )
    error_type: str | None = Field(
        default=None,
        min_length=1,
        description="可选错误类型摘要。",
    )
    safe_fields: JsonMap = Field(
        default_factory=dict,
        description="允许输出到结构化日志的安全字段。",
    )


__all__: tuple[str, ...] = (
    "JsonMap",
    "MetricEvent",
    "ObservabilityContext",
    "ObservabilityError",
    "ObservabilityErrorDto",
    "StructuredLogEvent",
    "TechnicalSpan",
    "build_observability_error_dto",
)
