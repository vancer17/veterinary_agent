##################################################################################################
# 文件: src/veterinary_agent/observability/enums.py
# 作用: 定义 Observability 组件稳定枚举，包括错误码、操作名、指标类型与 span 状态。
# 边界: 仅承载枚举定义；不执行指标记录、日志输出或 exporter 调用。
##################################################################################################

from enum import StrEnum


class ObservabilityErrorCode(StrEnum):
    """Observability 稳定错误码。"""

    OBS_METRIC_NAME_INVALID = "OBS_METRIC_NAME_INVALID"
    OBS_LABEL_REJECTED = "OBS_LABEL_REJECTED"
    OBS_CONTEXT_MISSING = "OBS_CONTEXT_MISSING"
    OBS_SPAN_RELATION_INVALID = "OBS_SPAN_RELATION_INVALID"
    OBS_EXPORTER_UNAVAILABLE = "OBS_EXPORTER_UNAVAILABLE"
    OBS_EVENT_UNSAFE = "OBS_EVENT_UNSAFE"
    OBS_METRICS_ENDPOINT_UNAVAILABLE = "OBS_METRICS_ENDPOINT_UNAVAILABLE"


class ObservabilityOperation(StrEnum):
    """Observability 对外操作名。"""

    START_REQUEST_OBSERVATION = "StartRequestObservation"
    FINISH_REQUEST_OBSERVATION = "FinishRequestObservation"
    START_SPAN = "StartSpan"
    FINISH_SPAN = "FinishSpan"
    RECORD_METRIC = "RecordMetric"
    RECORD_EVENT = "RecordEvent"
    RECORD_ERROR = "RecordError"
    RECORD_LLM_CALL = "RecordLlmCall"
    RECORD_TOOL_CALL = "RecordToolCall"
    RECORD_SEGMENT_PUBLISH = "RecordSegmentPublish"
    RENDER_METRICS = "RenderMetrics"


class MetricType(StrEnum):
    """Observability 指标类型。"""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class SpanStatus(StrEnum):
    """技术 span 状态。"""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class StructuredLogLevel(StrEnum):
    """结构化日志级别。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


__all__: tuple[str, ...] = (
    "MetricType",
    "ObservabilityErrorCode",
    "ObservabilityOperation",
    "SpanStatus",
    "StructuredLogLevel",
)
