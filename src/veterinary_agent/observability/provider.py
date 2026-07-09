##################################################################################################
# 文件: src/veterinary_agent/observability/provider.py
# 作用: 实现 Observability 应用内 provider，统一封装请求观测、span、metrics、事件日志与降级策略。
# 边界: 不实现业务逻辑链留痕、不保存敏感正文、不直接接入未实现的 L1/L2 领域组件。
##################################################################################################

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from threading import RLock
from time import perf_counter
from uuid import uuid4

from veterinary_agent.config import ObservabilitySettings
from veterinary_agent.observability.dto import (
    JsonMap,
    MetricEvent,
    ObservabilityContext,
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
    InMemoryMetricCollector,
    MetricCollectorError,
)

_CURRENT_CONTEXT: ContextVar[ObservabilityContext | None] = ContextVar(
    "veterinary_agent_observability_context",
    default=None,
)
_CURRENT_SPAN_ID: ContextVar[str | None] = ContextVar(
    "veterinary_agent_observability_span_id",
    default=None,
)

_SENSITIVE_FIELD_PARTS: frozenset[str] = frozenset(
    {
        "api_key",
        "authorization",
        "completion",
        "connection",
        "credential",
        "database_url",
        "dsn",
        "medical_record",
        "ocr_text",
        "password",
        "prompt",
        "secret",
        "storage_ref",
        "token",
        "transcript",
    }
)


@dataclass(frozen=True, slots=True)
class RequestObservationHandle:
    """请求观测句柄。"""

    context: ObservabilityContext
    context_token: Token[ObservabilityContext | None]
    endpoint: str
    method: str
    streaming: bool
    started_monotonic: float
    excluded_from_metrics: bool


@dataclass(frozen=True, slots=True)
class SpanObservationHandle:
    """技术 span 观测句柄。"""

    span: TechnicalSpan
    span_token: Token[str | None]
    started_monotonic: float


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


def _build_span_id() -> str:
    """生成本地技术 span ID。

    :return: 带 span_ 前缀的随机 span ID。
    """

    return f"span_{uuid4().hex}"


def _is_sensitive_field_name(field_name: str) -> bool:
    """判断字段名是否疑似敏感。

    :param field_name: 待检查字段名。
    :return: 若字段名包含敏感片段则返回 True。
    """

    normalized_name = field_name.lower()
    return any(part in normalized_name for part in _SENSITIVE_FIELD_PARTS)


def _safe_json_value(value: object, *, max_bytes: int) -> object:
    """将结构化日志字段裁剪为安全大小。

    :param value: 原始字段值。
    :param max_bytes: 单字段允许的最大 UTF-8 字节数。
    :return: 未超限时返回原值；超限时返回裁剪摘要。
    """

    rendered_value = json.dumps(value, ensure_ascii=True, default=str)
    if len(rendered_value.encode("utf-8")) <= max_bytes:
        return value
    return {
        "truncated": True,
        "original_bytes": len(rendered_value.encode("utf-8")),
    }


def _normalize_log_level(level: StructuredLogLevel) -> int:
    """转换结构化日志级别为 Python logging 级别。

    :param level: Observability 结构化日志级别。
    :return: Python logging 整数级别。
    """

    return getattr(logging, level.value)


class ObservabilityProvider:
    """Observability 应用内 provider。"""

    def __init__(self, *, settings: ObservabilitySettings) -> None:
        """初始化 Observability provider。

        :param settings: 已校验的 Observability RuntimeConfig。
        :return: None。
        """

        self.settings = settings
        self._collector = InMemoryMetricCollector(
            default_buckets=settings.metrics.duration_buckets_seconds,
        )
        self._logger = logging.getLogger("veterinary_agent.observability")
        self._logger.setLevel(settings.logging.level)
        self._active_request_counts: dict[tuple[str, str, str], int] = {}
        self._lock = RLock()
        self._ready = True
        self._tracing_backend_available = False
        self._tracing_degraded = False
        self._register_builtin_metrics()
        self._initialize_tracing_backend()

    def _register_builtin_metrics(self) -> None:
        """注册 Observability 内置指标。

        :return: None。
        """

        if not self.settings.metrics.enabled:
            return
        self._collector.register_metric(
            name="http_requests_total",
            description="HTTP 请求总数。",
            metric_type=MetricType.COUNTER,
            label_names=("endpoint", "method", "status_code", "streaming"),
        )
        self._collector.register_metric(
            name="http_request_duration_seconds",
            description="HTTP 请求处理耗时，单位为秒。",
            metric_type=MetricType.HISTOGRAM,
            label_names=("endpoint", "method", "status_code", "streaming"),
        )
        self._collector.register_metric(
            name="http_request_errors_total",
            description="HTTP 错误请求总数。",
            metric_type=MetricType.COUNTER,
            label_names=("endpoint", "error_type", "status_code"),
        )
        self._collector.register_metric(
            name="http_active_requests",
            description="当前活跃 HTTP 请求数。",
            metric_type=MetricType.GAUGE,
            label_names=("endpoint", "method", "streaming"),
        )
        self._collector.register_metric(
            name="observability_exporter_errors_total",
            description="Observability exporter 或本地记录错误总数。",
            metric_type=MetricType.COUNTER,
            label_names=("exporter_type",),
        )

    def is_ready(self) -> bool:
        """判断 Observability provider 是否就绪。

        :return: 若 provider 已初始化且可安全降级运行，则返回 True。
        """

        return self._ready

    def tracing_degraded(self) -> bool:
        """判断 tracing backend 是否处于降级状态。

        :return: 若 tracing 已启用但后端不可用，则返回 True。
        """

        return self._tracing_degraded

    def tracing_backend_available(self) -> bool:
        """判断 OpenTelemetry tracing backend 是否可用。

        :return: 若 tracing 已启用且 OpenTelemetry 包可导入，则返回 True。
        """

        return self._tracing_backend_available

    def _initialize_tracing_backend(self) -> None:
        """初始化 OpenTelemetry tracing backend 探测状态。

        :return: None。
        """

        if not self.settings.tracing.enabled:
            return
        try:
            __import__("opentelemetry")
        except ModuleNotFoundError:
            self._tracing_degraded = True
            self._record_exporter_error(exporter_type="opentelemetry")
            self.record_event(
                event_name="observability.tracing.degraded",
                component="Observability",
                level=StructuredLogLevel.WARNING,
                safe_fields={
                    "reason": "opentelemetry_package_missing",
                    "service_name": self.settings.tracing.service_name,
                    "environment": self.settings.tracing.environment,
                },
            )
            return
        self._tracing_backend_available = True
        self.record_event(
            event_name="observability.tracing.ready",
            component="Observability",
            safe_fields={
                "service_name": self.settings.tracing.service_name,
                "environment": self.settings.tracing.environment,
                "sample_rate": self.settings.tracing.sample_rate,
            },
        )

    def current_context(self) -> ObservabilityContext | None:
        """读取当前上下文变量中的 ObservabilityContext。

        :return: 当前请求上下文；若不在观测请求内则返回 None。
        """

        return _CURRENT_CONTEXT.get()

    def metrics_endpoint_enabled(self) -> bool:
        """判断 metrics endpoint 是否启用。

        :return: 若 Observability、metrics 与 endpoint 均启用，则返回 True。
        """

        return (
            self.settings.enabled
            and self.settings.metrics.enabled
            and self.settings.metrics.endpoint_enabled
        )

    def should_exclude_path(self, path: str) -> bool:
        """判断请求路径是否应排除在 HTTP metrics 外。

        :param path: 当前请求路径。
        :return: 若路径在排除列表中则返回 True。
        """

        return path in set(self.settings.metrics.exclude_paths)

    def _truncate_label_value(self, value: str) -> str:
        """裁剪指标 label 值到配置允许长度。

        :param value: 原始 label 值。
        :return: 裁剪后的 label 值。
        """

        max_length = self.settings.metrics.max_label_value_length
        if len(value) <= max_length:
            return value
        return value[:max_length]

    def _build_error(
        self,
        *,
        code: ObservabilityErrorCode,
        operation: ObservabilityOperation,
        message: str,
        retryable: bool,
        conflict_with: JsonMap | None = None,
    ) -> ObservabilityErrorDto:
        """构建并记录 Observability 降级错误。

        :param code: Observability 稳定错误码。
        :param operation: 发生错误的操作名。
        :param message: 面向工程排障的错误说明。
        :param retryable: 调用方是否可稍后重试。
        :param conflict_with: 冲突对象摘要。
        :return: Observability 统一错误 DTO。
        """

        error = build_observability_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            conflict_with=conflict_with,
        )
        self._record_exporter_error(exporter_type="local")
        self.record_event(
            event_name="observability.degraded",
            component="Observability",
            level=StructuredLogLevel.WARNING,
            safe_fields={"error": error.model_dump(mode="json")},
        )
        return error

    def _record_exporter_error(self, *, exporter_type: str) -> None:
        """记录 Observability 自身记录错误。

        :param exporter_type: 发生错误的 exporter 类型。
        :return: None。
        """

        if not self.settings.metrics.enabled:
            return
        try:
            self._collector.increment_counter(
                name="observability_exporter_errors_total",
                labels={"exporter_type": exporter_type},
                description="Observability exporter 或本地记录错误总数。",
            )
        except MetricCollectorError:
            return

    def _sanitize_metric_labels(
        self,
        *,
        labels: dict[str, str],
        operation: ObservabilityOperation,
    ) -> tuple[dict[str, str] | None, ObservabilityErrorDto | None]:
        """校验并裁剪指标 label。

        :param labels: 原始指标 label 集合。
        :param operation: 当前记录指标的操作名。
        :return: 已清理 label 与可选错误；存在错误时 label 返回 None。
        """

        allowed_labels = set(self.settings.label_policy.allowed_metric_labels)
        forbidden_labels = set(self.settings.label_policy.forbidden_metric_labels)
        sanitized_labels: dict[str, str] = {}
        for label_name, label_value in labels.items():
            if label_name in forbidden_labels:
                return None, self._build_error(
                    code=ObservabilityErrorCode.OBS_LABEL_REJECTED,
                    operation=operation,
                    message="指标 label 命中禁止列表",
                    retryable=False,
                    conflict_with={"label": label_name},
                )
            if (
                label_name not in allowed_labels
                and not self.settings.label_policy.allow_unlisted_labels
            ):
                return None, self._build_error(
                    code=ObservabilityErrorCode.OBS_LABEL_REJECTED,
                    operation=operation,
                    message="指标 label 不在白名单中",
                    retryable=False,
                    conflict_with={"label": label_name},
                )
            sanitized_labels[label_name] = self._truncate_label_value(str(label_value))
        return sanitized_labels, None

    def _sanitize_safe_fields(
        self,
        *,
        fields: JsonMap,
        operation: ObservabilityOperation,
    ) -> tuple[JsonMap | None, ObservabilityErrorDto | None]:
        """校验并裁剪结构化日志安全字段。

        :param fields: 原始结构化字段。
        :param operation: 当前记录事件的操作名。
        :return: 已清理字段与可选错误；存在错误时字段返回 None。
        """

        sanitized_fields: JsonMap = {}
        for field_name, field_value in fields.items():
            if _is_sensitive_field_name(field_name):
                return None, self._build_error(
                    code=ObservabilityErrorCode.OBS_EVENT_UNSAFE,
                    operation=operation,
                    message="结构化事件包含敏感字段名",
                    retryable=False,
                    conflict_with={"field": field_name},
                )
            sanitized_fields[field_name] = _safe_json_value(
                field_value,
                max_bytes=self.settings.logging.max_field_bytes,
            )
        return sanitized_fields, None

    def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: MetricType,
        labels: dict[str, str] | None = None,
        description: str = "Observability metric.",
    ) -> ObservabilityErrorDto | None:
        """记录通用指标事件。

        :param metric_name: 指标名称。
        :param value: 指标观测值。
        :param metric_type: 指标类型。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: 记录成功时返回 None；降级时返回 Observability 错误 DTO。
        """

        if not self.settings.enabled or not self.settings.metrics.enabled:
            return None
        sanitized_labels, error = self._sanitize_metric_labels(
            labels=labels or {},
            operation=ObservabilityOperation.RECORD_METRIC,
        )
        if error is not None or sanitized_labels is None:
            return error
        try:
            MetricEvent(
                metric_name=metric_name,
                value=value,
                metric_type=metric_type,
                low_cardinality_labels=sanitized_labels,
            )
            if metric_type is MetricType.COUNTER:
                self._collector.increment_counter(
                    name=metric_name,
                    amount=value,
                    labels=sanitized_labels,
                    description=description,
                )
            elif metric_type is MetricType.GAUGE:
                self._collector.set_gauge(
                    name=metric_name,
                    value=value,
                    labels=sanitized_labels,
                    description=description,
                )
            elif metric_type is MetricType.HISTOGRAM:
                self._collector.observe_histogram(
                    name=metric_name,
                    value=value,
                    labels=sanitized_labels,
                    description=description,
                )
        except (MetricCollectorError, ValueError) as exc:
            return self._build_error(
                code=ObservabilityErrorCode.OBS_METRIC_NAME_INVALID,
                operation=ObservabilityOperation.RECORD_METRIC,
                message=str(exc),
                retryable=False,
                conflict_with={"metric_name": metric_name},
            )
        return None

    def record_event(
        self,
        *,
        event_name: str,
        component: str,
        level: StructuredLogLevel = StructuredLogLevel.INFO,
        safe_fields: JsonMap | None = None,
        error_type: str | None = None,
    ) -> ObservabilityErrorDto | None:
        """记录结构化运行事件。

        :param event_name: 事件名称。
        :param component: 产生事件的组件名。
        :param level: 结构化日志级别。
        :param safe_fields: 允许输出到日志的安全字段。
        :param error_type: 可选错误类型摘要。
        :return: 记录成功时返回 None；降级时返回 Observability 错误 DTO。
        """

        if not self.settings.enabled or not self.settings.logging.enabled:
            return None
        sanitized_fields, error = self._sanitize_safe_fields(
            fields=safe_fields or {},
            operation=ObservabilityOperation.RECORD_EVENT,
        )
        if error is not None or sanitized_fields is None:
            return error
        context = self.current_context()
        event = StructuredLogEvent(
            level=level,
            event_name=event_name,
            component=component,
            error_type=error_type,
            safe_fields=sanitized_fields,
        )
        payload: JsonMap = {
            "timestamp": _now_utc().isoformat(),
            "level": event.level.value,
            "event_name": event.event_name,
            "component": event.component,
            "error_type": event.error_type,
            "fields": event.safe_fields,
        }
        if context is not None:
            payload["request_id"] = context.request_id
            payload["trace_id"] = context.trace_id
            payload["config_snapshot_id"] = context.config_snapshot_id
            payload["params_version"] = context.params_version
        self._logger.log(
            _normalize_log_level(level),
            json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str),
        )
        return None

    def record_error(
        self,
        *,
        component: str,
        error_type: str,
        error_message: str,
        safe_fields: JsonMap | None = None,
    ) -> ObservabilityErrorDto | None:
        """记录异常摘要事件。

        :param component: 产生异常的组件名。
        :param error_type: 错误类型摘要。
        :param error_message: 脱敏后的错误说明。
        :param safe_fields: 允许输出到日志的安全字段。
        :return: 记录成功时返回 None；降级时返回 Observability 错误 DTO。
        """

        merged_fields: JsonMap = {
            **(safe_fields or {}),
            "error_message": error_message,
        }
        return self.record_event(
            event_name="error",
            component=component,
            level=StructuredLogLevel.ERROR,
            safe_fields=merged_fields,
            error_type=error_type,
        )

    def start_request(
        self,
        *,
        request_id: str,
        trace_id: str,
        endpoint: str,
        method: str,
        streaming: bool,
        config_snapshot_id: str | None = None,
        params_version: str | None = None,
        safe_attributes: JsonMap | None = None,
    ) -> RequestObservationHandle:
        """开始一次 HTTP 请求观测。

        :param request_id: 本次入口请求 ID。
        :param trace_id: 本系统业务链路关联 ID。
        :param endpoint: HTTP 路径或 route template。
        :param method: HTTP 方法。
        :param streaming: 当前请求是否为流式响应。
        :param config_snapshot_id: 可选 RuntimeConfig 快照 ID。
        :param params_version: 可选业务运行参数版本。
        :param safe_attributes: 允许进入日志与 trace 的安全上下文字段。
        :return: 请求观测句柄。
        """

        context = ObservabilityContext(
            request_id=request_id,
            trace_id=trace_id,
            config_snapshot_id=config_snapshot_id,
            params_version=params_version,
            started_at=_now_utc(),
            safe_attributes=safe_attributes or {},
        )
        context_token = _CURRENT_CONTEXT.set(context)
        excluded = self.should_exclude_path(endpoint)
        handle = RequestObservationHandle(
            context=context,
            context_token=context_token,
            endpoint=endpoint,
            method=method,
            streaming=streaming,
            started_monotonic=perf_counter(),
            excluded_from_metrics=excluded,
        )
        if not excluded:
            self._increment_active_request(handle)
        self.record_event(
            event_name="http.request.started",
            component="ApiIngress",
            safe_fields={
                "endpoint": endpoint,
                "method": method,
                "streaming": streaming,
            },
        )
        return handle

    def bind_request_identity(
        self,
        *,
        request_id: str,
        trace_id: str,
        safe_attributes: JsonMap | None = None,
    ) -> ObservabilityContext | None:
        """更新当前请求观测上下文中的最终请求身份。

        :param request_id: ApiIngress 解析后的最终 request_id。
        :param trace_id: ApiIngress 解析后的最终 trace_id。
        :param safe_attributes: 需要合并进上下文的安全字段。
        :return: 更新后的 ObservabilityContext；若上下文缺失则返回 None。
        """

        context = self.current_context()
        if context is None:
            self._build_error(
                code=ObservabilityErrorCode.OBS_CONTEXT_MISSING,
                operation=ObservabilityOperation.START_REQUEST_OBSERVATION,
                message="Observability 上下文缺失",
                retryable=True,
            )
            return None
        merged_attributes: JsonMap = {
            **context.safe_attributes,
            **(safe_attributes or {}),
        }
        updated_context = context.model_copy(
            update={
                "request_id": request_id,
                "trace_id": trace_id,
                "safe_attributes": merged_attributes,
            }
        )
        _CURRENT_CONTEXT.set(updated_context)
        return updated_context

    def _increment_active_request(self, handle: RequestObservationHandle) -> None:
        """增加活跃请求 gauge。

        :param handle: 请求观测句柄。
        :return: None。
        """

        labels = {
            "endpoint": handle.endpoint,
            "method": handle.method,
            "streaming": str(handle.streaming).lower(),
        }
        key = (handle.endpoint, handle.method, str(handle.streaming).lower())
        with self._lock:
            self._active_request_counts[key] = (
                self._active_request_counts.get(key, 0) + 1
            )
            active_count = self._active_request_counts[key]
        self.record_metric(
            metric_name="http_active_requests",
            value=float(active_count),
            metric_type=MetricType.GAUGE,
            labels=labels,
            description="当前活跃 HTTP 请求数。",
        )

    def _decrement_active_request(self, handle: RequestObservationHandle) -> None:
        """减少活跃请求 gauge。

        :param handle: 请求观测句柄。
        :return: None。
        """

        labels = {
            "endpoint": handle.endpoint,
            "method": handle.method,
            "streaming": str(handle.streaming).lower(),
        }
        key = (handle.endpoint, handle.method, str(handle.streaming).lower())
        with self._lock:
            next_count = max(self._active_request_counts.get(key, 0) - 1, 0)
            self._active_request_counts[key] = next_count
        self.record_metric(
            metric_name="http_active_requests",
            value=float(next_count),
            metric_type=MetricType.GAUGE,
            labels=labels,
            description="当前活跃 HTTP 请求数。",
        )

    def finish_request(
        self,
        *,
        handle: RequestObservationHandle,
        status_code: int,
        error_type: str | None = None,
    ) -> None:
        """完成一次 HTTP 请求观测。

        :param handle: 请求观测句柄。
        :param status_code: HTTP 响应状态码。
        :param error_type: 可选错误类型摘要。
        :return: None。
        """

        duration_seconds = perf_counter() - handle.started_monotonic
        streaming_value = str(handle.streaming).lower()
        status_code_value = str(status_code)
        if not handle.excluded_from_metrics:
            labels = {
                "endpoint": handle.endpoint,
                "method": handle.method,
                "status_code": status_code_value,
                "streaming": streaming_value,
            }
            self.record_metric(
                metric_name="http_requests_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="HTTP 请求总数。",
            )
            self.record_metric(
                metric_name="http_request_duration_seconds",
                value=duration_seconds,
                metric_type=MetricType.HISTOGRAM,
                labels=labels,
                description="HTTP 请求处理耗时，单位为秒。",
            )
            if status_code >= 400:
                self.record_metric(
                    metric_name="http_request_errors_total",
                    value=1.0,
                    metric_type=MetricType.COUNTER,
                    labels={
                        "endpoint": handle.endpoint,
                        "status_code": status_code_value,
                        "error_type": error_type or "http_error",
                    },
                    description="HTTP 错误请求总数。",
                )
            self._decrement_active_request(handle)
        self.record_event(
            event_name="http.request.finished",
            component="ApiIngress",
            safe_fields={
                "endpoint": handle.endpoint,
                "method": handle.method,
                "status_code": status_code,
                "streaming": handle.streaming,
                "duration_ms": round(duration_seconds * 1000, 3),
            },
            error_type=error_type,
        )
        _CURRENT_CONTEXT.reset(handle.context_token)

    def start_span(
        self,
        *,
        span_name: str,
        component: str,
        parent_span_id: str | None = None,
        safe_attributes: JsonMap | None = None,
    ) -> SpanObservationHandle:
        """开始一个技术 span。

        :param span_name: 技术 span 名称。
        :param component: 产生 span 的组件名。
        :param parent_span_id: 可选父级 span ID；未传入时使用当前上下文 span。
        :param safe_attributes: 允许进入日志与 trace 的安全 span 属性。
        :return: 技术 span 观测句柄。
        """

        resolved_parent_span_id = parent_span_id or _CURRENT_SPAN_ID.get()
        span = TechnicalSpan(
            span_id=_build_span_id(),
            parent_span_id=resolved_parent_span_id,
            span_name=span_name,
            component=component,
            started_at=_now_utc(),
            safe_attributes=safe_attributes or {},
        )
        span_token = _CURRENT_SPAN_ID.set(span.span_id)
        self.record_event(
            event_name="span.started",
            component=component,
            safe_fields={
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "span_name": span.span_name,
                **span.safe_attributes,
            },
        )
        return SpanObservationHandle(
            span=span,
            span_token=span_token,
            started_monotonic=perf_counter(),
        )

    def finish_span(
        self,
        *,
        handle: SpanObservationHandle,
        status: SpanStatus = SpanStatus.SUCCEEDED,
        error_type: str | None = None,
    ) -> None:
        """完成一个技术 span。

        :param handle: 技术 span 观测句柄。
        :param status: span 完成状态。
        :param error_type: 可选错误类型摘要。
        :return: None。
        """

        duration_seconds = perf_counter() - handle.started_monotonic
        duration_ms = int(duration_seconds * 1000)
        self.record_metric(
            metric_name="technical_span_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels={
                "component": handle.span.component,
                "status": status.value,
            },
            description="技术 span 耗时，单位为秒。",
        )
        self.record_event(
            event_name="span.finished",
            component=handle.span.component,
            safe_fields={
                "span_id": handle.span.span_id,
                "parent_span_id": handle.span.parent_span_id,
                "span_name": handle.span.span_name,
                "status": status.value,
                "duration_ms": duration_ms,
                **handle.span.safe_attributes,
            },
            error_type=error_type,
        )
        _CURRENT_SPAN_ID.reset(handle.span_token)

    def record_llm_call(
        self,
        *,
        agent_name: str,
        generation_profile: str,
        model_provider: str,
        model_name: str,
        status: str,
        duration_seconds: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        retry_count: int = 0,
        error_type: str | None = None,
    ) -> None:
        """记录 LLM 调用技术摘要。

        :param agent_name: 调用 LLM 的 Agent 名称。
        :param generation_profile: 本次生成剖面。
        :param model_provider: 模型供应商标识。
        :param model_name: 模型标识。
        :param status: 调用状态。
        :param duration_seconds: 调用耗时，单位为秒。
        :param prompt_tokens: prompt token 数量。
        :param completion_tokens: completion token 数量。
        :param retry_count: 重试次数。
        :param error_type: 可选错误类型摘要。
        :return: None。
        """

        labels = {
            "agent_name": agent_name,
            "generation_profile": generation_profile,
            "model_provider": model_provider,
            "model_name": model_name,
            "status": status,
        }
        self.record_metric(
            metric_name="llm_calls_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="模型调用总数。",
        )
        self.record_metric(
            metric_name="llm_call_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="模型调用耗时，单位为秒。",
        )
        token_labels = {
            "agent_name": agent_name,
            "generation_profile": generation_profile,
            "model_provider": model_provider,
            "model_name": model_name,
            "status": status,
        }
        self.record_metric(
            metric_name="llm_prompt_tokens_total",
            value=float(prompt_tokens),
            metric_type=MetricType.COUNTER,
            labels=token_labels,
            description="prompt token 总量。",
        )
        self.record_metric(
            metric_name="llm_completion_tokens_total",
            value=float(completion_tokens),
            metric_type=MetricType.COUNTER,
            labels=token_labels,
            description="completion token 总量。",
        )
        self.record_metric(
            metric_name="llm_total_tokens_total",
            value=float(prompt_tokens + completion_tokens),
            metric_type=MetricType.COUNTER,
            labels=token_labels,
            description="模型调用总 token 消耗。",
        )
        if retry_count:
            self.record_metric(
                metric_name="llm_call_retries_total",
                value=float(retry_count),
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="模型调用重试次数。",
            )
        self.record_event(
            event_name="llm.call.finished",
            component="LlmGateway",
            safe_fields={
                "agent_name": agent_name,
                "generation_profile": generation_profile,
                "model_provider": model_provider,
                "model_name": model_name,
                "status": status,
                "duration_ms": round(duration_seconds * 1000, 3),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "retry_count": retry_count,
            },
            error_type=error_type,
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        duration_seconds: float,
        timed_out: bool = False,
        error_type: str | None = None,
    ) -> None:
        """记录工具调用技术摘要。

        :param tool_name: 工具名称。
        :param status: 调用状态。
        :param duration_seconds: 调用耗时，单位为秒。
        :param timed_out: 当前工具调用是否超时。
        :param error_type: 可选错误类型摘要。
        :return: None。
        """

        labels = {"tool_name": tool_name, "status": status}
        self.record_metric(
            metric_name="tool_calls_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="工具调用总数。",
        )
        self.record_metric(
            metric_name="tool_call_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="工具调用耗时，单位为秒。",
        )
        if timed_out:
            self.record_metric(
                metric_name="tool_call_timeouts_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="工具调用超时次数。",
            )
        self.record_event(
            event_name="tool.call.finished",
            component="ToolRegistry",
            safe_fields={
                "tool_name": tool_name,
                "status": status,
                "duration_ms": round(duration_seconds * 1000, 3),
                "timed_out": timed_out,
            },
            error_type=error_type,
        )

    def record_segment_publish(
        self,
        *,
        segment_type: str,
        generation_profile: str,
        is_first_segment: bool,
        status: str,
        duration_seconds: float,
    ) -> None:
        """记录流式 segment 发布技术摘要。

        :param segment_type: segment 类型。
        :param generation_profile: 本次生成剖面。
        :param is_first_segment: 当前 segment 是否为首段。
        :param status: 发布状态。
        :param duration_seconds: 发布耗时，单位为秒。
        :return: None。
        """

        labels = {
            "segment_type": segment_type,
            "generation_profile": generation_profile,
            "is_first_segment": str(is_first_segment).lower(),
            "status": status,
        }
        self.record_metric(
            metric_name="segments_published_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="segment 发布总数。",
        )
        self.record_metric(
            metric_name="segment_publish_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="segment 发布耗时，单位为秒。",
        )
        if is_first_segment:
            self.record_metric(
                metric_name="stream_first_byte_duration_seconds",
                value=duration_seconds,
                metric_type=MetricType.HISTOGRAM,
                labels=labels,
                description="流式首段发布时间，单位为秒。",
            )
        self.record_event(
            event_name="segment.publish.finished",
            component="VetResponseComposer",
            safe_fields={
                "segment_type": segment_type,
                "generation_profile": generation_profile,
                "is_first_segment": is_first_segment,
                "status": status,
                "duration_ms": round(duration_seconds * 1000, 3),
            },
        )

    def render_prometheus_metrics(self) -> str:
        """渲染 Prometheus metrics endpoint 内容。

        :return: Prometheus 文本格式指标。
        """

        if not self.metrics_endpoint_enabled():
            self._build_error(
                code=ObservabilityErrorCode.OBS_METRICS_ENDPOINT_UNAVAILABLE,
                operation=ObservabilityOperation.RENDER_METRICS,
                message="metrics endpoint 未启用",
                retryable=False,
            )
            return ""
        return self._collector.render_prometheus()


def create_observability_provider(
    *,
    settings: ObservabilitySettings,
) -> ObservabilityProvider:
    """创建 Observability 应用内 provider。

    :param settings: 已校验的 Observability RuntimeConfig。
    :return: Observability provider。
    """

    return ObservabilityProvider(settings=settings)


__all__: tuple[str, ...] = (
    "ObservabilityProvider",
    "RequestObservationHandle",
    "SpanObservationHandle",
    "create_observability_provider",
)
