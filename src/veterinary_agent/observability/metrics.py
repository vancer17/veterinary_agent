##################################################################################################
# 文件: src/veterinary_agent/observability/metrics.py
# 作用: 提供 Observability MVP 的进程内指标聚合器与 Prometheus 文本格式渲染能力。
# 边界: 不实现指标长期存储、查询语言、告警或看板；不依赖外部 Prometheus server。
##################################################################################################

import math
import re
from dataclasses import dataclass, field
from threading import RLock
from typing import Final

from veterinary_agent.observability.enums import MetricType

PROMETHEUS_CONTENT_TYPE: Final[str] = "text/plain; version=0.0.4; charset=utf-8"

_METRIC_NAME_PATTERN = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_HELP_ESCAPE_TABLE: Final[dict[str, str]] = {
    "\\": "\\\\",
    "\n": "\\n",
}
_LABEL_ESCAPE_TABLE: Final[dict[str, str]] = {
    "\\": "\\\\",
    "\n": "\\n",
    '"': '\\"',
}


class MetricCollectorError(ValueError):
    """进程内指标聚合器错误。"""


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """进程内指标定义。"""

    name: str
    description: str
    metric_type: MetricType
    label_names: tuple[str, ...]
    buckets: tuple[float, ...] = ()


@dataclass(slots=True)
class HistogramSample:
    """进程内 histogram 样本。"""

    bucket_counts: dict[float, int] = field(default_factory=dict)
    positive_infinity_count: int = 0
    count: int = 0
    total_sum: float = 0.0


def _validate_metric_name(name: str) -> None:
    """校验 Prometheus 指标名称。

    :param name: 待校验的指标名称。
    :return: None。
    :raises MetricCollectorError: 当指标名称不符合 Prometheus 基础格式时抛出。
    """

    if _METRIC_NAME_PATTERN.fullmatch(name) is None:
        raise MetricCollectorError("metric name invalid")


def _escape_help_text(value: str) -> str:
    """转义 Prometheus HELP 文本。

    :param value: 原始 HELP 文本。
    :return: 已转义的 HELP 文本。
    """

    escaped_value = value
    for source, target in _HELP_ESCAPE_TABLE.items():
        escaped_value = escaped_value.replace(source, target)
    return escaped_value


def _escape_label_value(value: str) -> str:
    """转义 Prometheus label 值。

    :param value: 原始 label 值。
    :return: 已转义的 label 值。
    """

    escaped_value = value
    for source, target in _LABEL_ESCAPE_TABLE.items():
        escaped_value = escaped_value.replace(source, target)
    return escaped_value


def _format_float(value: float) -> str:
    """格式化 Prometheus 浮点值。

    :param value: 需要格式化的浮点值。
    :return: Prometheus exposition 可接受的浮点字符串。
    """

    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    return format(value, ".17g")


def _format_labels(labels: dict[str, str]) -> str:
    """格式化 Prometheus label 集合。

    :param labels: label 名称到 label 值的映射。
    :return: Prometheus exposition 中的 label 片段。
    """

    if not labels:
        return ""
    rendered_labels = ",".join(
        f'{name}="{_escape_label_value(value)}"'
        for name, value in sorted(labels.items())
    )
    return f"{{{rendered_labels}}}"


def _sample_key(
    *,
    definition: MetricDefinition,
    labels: dict[str, str],
) -> tuple[str, ...]:
    """根据指标定义构建样本键。

    :param definition: 指标定义。
    :param labels: 低基数 label 集合。
    :return: 与指标定义 label 顺序一致的样本键。
    """

    return tuple(labels.get(name, "") for name in definition.label_names)


def _labels_from_key(
    *,
    definition: MetricDefinition,
    sample_key: tuple[str, ...],
) -> dict[str, str]:
    """根据样本键还原 Prometheus label 集合。

    :param definition: 指标定义。
    :param sample_key: 与指标定义 label 顺序一致的样本键。
    :return: label 名称到 label 值的映射。
    """

    return dict(zip(definition.label_names, sample_key, strict=True))


class InMemoryMetricCollector:
    """进程内 Prometheus 风格指标聚合器。"""

    def __init__(self, *, default_buckets: list[float]) -> None:
        """初始化进程内指标聚合器。

        :param default_buckets: histogram 默认桶边界，单位为秒。
        :return: None。
        """

        self._default_buckets = tuple(default_buckets)
        self._definitions: dict[str, MetricDefinition] = {}
        self._counters: dict[str, dict[tuple[str, ...], float]] = {}
        self._gauges: dict[str, dict[tuple[str, ...], float]] = {}
        self._histograms: dict[str, dict[tuple[str, ...], HistogramSample]] = {}
        self._lock = RLock()

    def register_metric(
        self,
        *,
        name: str,
        description: str,
        metric_type: MetricType,
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        """注册指标定义。

        :param name: 指标名称。
        :param description: 指标 HELP 文本。
        :param metric_type: 指标类型。
        :param label_names: 指标 label 名称元组。
        :param buckets: histogram 桶边界；仅 histogram 使用。
        :return: None。
        :raises MetricCollectorError: 当指标名称非法或同名指标定义冲突时抛出。
        """

        _validate_metric_name(name)
        resolved_buckets = (
            tuple(buckets)
            if buckets is not None
            else (self._default_buckets if metric_type is MetricType.HISTOGRAM else ())
        )
        definition = MetricDefinition(
            name=name,
            description=description,
            metric_type=metric_type,
            label_names=tuple(label_names),
            buckets=resolved_buckets,
        )
        with self._lock:
            existing_definition = self._definitions.get(name)
            if existing_definition is not None and existing_definition != definition:
                raise MetricCollectorError("metric definition conflict")
            self._definitions[name] = definition

    def _get_or_register_definition(
        self,
        *,
        name: str,
        description: str,
        metric_type: MetricType,
        labels: dict[str, str],
    ) -> MetricDefinition:
        """读取或动态注册指标定义。

        :param name: 指标名称。
        :param description: 指标 HELP 文本。
        :param metric_type: 指标类型。
        :param labels: 本次观测携带的 label 集合。
        :return: 指标定义。
        :raises MetricCollectorError: 当指标定义冲突时抛出。
        """

        label_names = tuple(sorted(labels))
        with self._lock:
            definition = self._definitions.get(name)
            if definition is None:
                self.register_metric(
                    name=name,
                    description=description,
                    metric_type=metric_type,
                    label_names=label_names,
                )
                definition = self._definitions[name]
            if definition.metric_type is not metric_type:
                raise MetricCollectorError("metric type conflict")
            if definition.label_names != label_names:
                raise MetricCollectorError("metric label definition conflict")
            return definition

    def increment_counter(
        self,
        *,
        name: str,
        amount: float = 1.0,
        labels: dict[str, str] | None = None,
        description: str = "Counter metric.",
    ) -> None:
        """累加 counter 指标。

        :param name: 指标名称。
        :param amount: 累加值。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: None。
        :raises MetricCollectorError: 当指标定义冲突或累加值为负数时抛出。
        """

        if amount < 0:
            raise MetricCollectorError("counter amount must be non-negative")
        resolved_labels = labels or {}
        definition = self._get_or_register_definition(
            name=name,
            description=description,
            metric_type=MetricType.COUNTER,
            labels=resolved_labels,
        )
        key = _sample_key(definition=definition, labels=resolved_labels)
        with self._lock:
            samples = self._counters.setdefault(name, {})
            samples[key] = samples.get(key, 0.0) + amount

    def set_gauge(
        self,
        *,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        description: str = "Gauge metric.",
    ) -> None:
        """设置 gauge 指标。

        :param name: 指标名称。
        :param value: gauge 当前值。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: None。
        :raises MetricCollectorError: 当指标定义冲突时抛出。
        """

        resolved_labels = labels or {}
        definition = self._get_or_register_definition(
            name=name,
            description=description,
            metric_type=MetricType.GAUGE,
            labels=resolved_labels,
        )
        key = _sample_key(definition=definition, labels=resolved_labels)
        with self._lock:
            self._gauges.setdefault(name, {})[key] = value

    def observe_histogram(
        self,
        *,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        description: str = "Histogram metric.",
    ) -> None:
        """记录 histogram 观测值。

        :param name: 指标名称。
        :param value: 观测值。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: None。
        :raises MetricCollectorError: 当指标定义冲突时抛出。
        """

        resolved_labels = labels or {}
        definition = self._get_or_register_definition(
            name=name,
            description=description,
            metric_type=MetricType.HISTOGRAM,
            labels=resolved_labels,
        )
        key = _sample_key(definition=definition, labels=resolved_labels)
        with self._lock:
            samples = self._histograms.setdefault(name, {})
            sample = samples.setdefault(
                key,
                HistogramSample(
                    bucket_counts={bucket: 0 for bucket in definition.buckets}
                ),
            )
            for bucket in definition.buckets:
                if value <= bucket:
                    sample.bucket_counts[bucket] = (
                        sample.bucket_counts.get(bucket, 0) + 1
                    )
            sample.positive_infinity_count += 1
            sample.count += 1
            sample.total_sum += value

    def _render_counter(
        self,
        *,
        definition: MetricDefinition,
        lines: list[str],
    ) -> None:
        """渲染 counter 指标。

        :param definition: counter 指标定义。
        :param lines: Prometheus exposition 输出行列表。
        :return: None。
        """

        for key, value in sorted(self._counters.get(definition.name, {}).items()):
            labels = _labels_from_key(definition=definition, sample_key=key)
            lines.append(
                f"{definition.name}{_format_labels(labels)} {_format_float(value)}"
            )

    def _render_gauge(
        self,
        *,
        definition: MetricDefinition,
        lines: list[str],
    ) -> None:
        """渲染 gauge 指标。

        :param definition: gauge 指标定义。
        :param lines: Prometheus exposition 输出行列表。
        :return: None。
        """

        for key, value in sorted(self._gauges.get(definition.name, {}).items()):
            labels = _labels_from_key(definition=definition, sample_key=key)
            lines.append(
                f"{definition.name}{_format_labels(labels)} {_format_float(value)}"
            )

    def _render_histogram(
        self,
        *,
        definition: MetricDefinition,
        lines: list[str],
    ) -> None:
        """渲染 histogram 指标。

        :param definition: histogram 指标定义。
        :param lines: Prometheus exposition 输出行列表。
        :return: None。
        """

        for key, sample in sorted(self._histograms.get(definition.name, {}).items()):
            base_labels = _labels_from_key(definition=definition, sample_key=key)
            for bucket in definition.buckets:
                labels = {**base_labels, "le": _format_float(bucket)}
                count = sample.bucket_counts.get(bucket, 0)
                lines.append(
                    f"{definition.name}_bucket{_format_labels(labels)} {count}"
                )
            labels = {**base_labels, "le": "+Inf"}
            lines.append(
                f"{definition.name}_bucket{_format_labels(labels)} "
                f"{sample.positive_infinity_count}"
            )
            lines.append(
                f"{definition.name}_count{_format_labels(base_labels)} {sample.count}"
            )
            lines.append(
                f"{definition.name}_sum{_format_labels(base_labels)} "
                f"{_format_float(sample.total_sum)}"
            )

    def render_prometheus(self) -> str:
        """渲染当前进程内指标为 Prometheus 文本格式。

        :return: Prometheus exposition 文本。
        """

        lines: list[str] = []
        with self._lock:
            definitions = sorted(self._definitions.values(), key=lambda item: item.name)
            for definition in definitions:
                lines.append(
                    f"# HELP {definition.name} "
                    f"{_escape_help_text(definition.description)}"
                )
                lines.append(f"# TYPE {definition.name} {definition.metric_type.value}")
                if definition.metric_type is MetricType.COUNTER:
                    self._render_counter(definition=definition, lines=lines)
                elif definition.metric_type is MetricType.GAUGE:
                    self._render_gauge(definition=definition, lines=lines)
                elif definition.metric_type is MetricType.HISTOGRAM:
                    self._render_histogram(definition=definition, lines=lines)
        return "\n".join(lines) + "\n"


__all__: tuple[str, ...] = (
    "InMemoryMetricCollector",
    "MetricCollectorError",
    "MetricDefinition",
    "PROMETHEUS_CONTENT_TYPE",
)
