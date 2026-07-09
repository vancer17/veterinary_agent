##################################################################################################
# 文件: src/veterinary_agent/observability/__init__.py
# 作用: 作为 Observability 包统一出口，向其他包暴露稳定 DTO、枚举、provider 与 metrics 常量。
# 边界: 外部包应从本文件导入可观测性能力，避免跨包直接引用实现模块。
##################################################################################################

from veterinary_agent.observability.dto import (
    JsonMap,
    MetricEvent,
    ObservabilityContext,
    ObservabilityError,
    ObservabilityErrorDto,
    StructuredLogEvent,
    TechnicalSpan,
    build_observability_error_dto,
)
from veterinary_agent.observability.enums import (
    MetricType,
    ObservabilityErrorCode,
    ObservabilityOperation,
    SpanStatus,
    StructuredLogLevel,
)
from veterinary_agent.observability.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    InMemoryMetricCollector,
    MetricCollectorError,
    MetricDefinition,
)
from veterinary_agent.observability.provider import (
    ObservabilityProvider,
    RequestObservationHandle,
    SpanObservationHandle,
    create_observability_provider,
)

__all__: tuple[str, ...] = (
    "InMemoryMetricCollector",
    "JsonMap",
    "MetricCollectorError",
    "MetricDefinition",
    "MetricEvent",
    "MetricType",
    "ObservabilityContext",
    "ObservabilityError",
    "ObservabilityErrorCode",
    "ObservabilityErrorDto",
    "ObservabilityOperation",
    "ObservabilityProvider",
    "PROMETHEUS_CONTENT_TYPE",
    "RequestObservationHandle",
    "SpanObservationHandle",
    "SpanStatus",
    "StructuredLogEvent",
    "StructuredLogLevel",
    "TechnicalSpan",
    "build_observability_error_dto",
    "create_observability_provider",
)
