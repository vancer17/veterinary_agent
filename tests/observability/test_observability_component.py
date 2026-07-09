##################################################################################################
# 文件: tests/observability/test_observability_component.py
# 作用: 验证 Observability 组件配置、RuntimeConfig 集成、provider 行为、Prometheus 暴露与 FastAPI 装配。
# 边界: 仅测试 L0 Observability MVP；不启动 Prometheus、Grafana、OpenTelemetry Collector 或未实现业务组件。
##################################################################################################

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import json
import logging
from logging import LogRecord
from logging.handlers import BufferingHandler
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from veterinary_agent import (
    ApiIngressSettings,
    MetricType,
    ObservabilityErrorCode,
    ObservabilityLabelPolicyConfig,
    ObservabilityMetricsConfig,
    ObservabilitySettings,
    ObservabilityTracingConfig,
    PROMETHEUS_CONTENT_TYPE,
    RuntimeConfigNamespace,
    SpanStatus,
    StructuredLogLevel,
    VeterinaryAgentAppState,
    create_app,
    create_observability_provider,
    create_runtime_config_provider,
    get_observability_provider,
    load_observability_settings,
)


def _settings_without_orchestrator_readiness() -> ApiIngressSettings:
    """构建关闭编排 TODO readiness 检查的 API 接入配置。

    :return: 已关闭编排 TODO readiness 检查的 API 接入配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "readiness": base_settings.readiness.model_copy(
                update={"check_orchestrator": False}
            )
        }
    )


def _agent_turn_payload(
    *,
    request_id: str | None = "req_obs_body",
    trace_id: str | None = "trace_obs_body",
) -> dict[str, object]:
    """构建测试用一轮 Agent 请求体。

    :param request_id: 可选请求 ID；为 None 时不写入请求体。
    :param trace_id: 可选链路 ID；为 None 时不写入请求体。
    :return: 可发送给 `/agent/turns` 的测试请求体。
    """

    payload: dict[str, object] = {
        "model": "vet-test-model",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "猫咪今天精神不好。",
                    }
                ],
            }
        ],
        "stream": False,
        "vet_context": {
            "user_id": "user_obs",
            "session_id": "session_obs",
            "pet_id": "pet_obs",
        },
    }
    if request_id is not None:
        payload["request_id"] = request_id
    if trace_id is not None:
        payload["trace_id"] = trace_id
    return payload


def _state_from_app(app: FastAPI) -> VeterinaryAgentAppState:
    """从 FastAPI app.state 读取兽医 Agent 应用状态。

    :param app: FastAPI 应用实例。
    :return: 兽医 Agent 应用状态。
    """

    state = getattr(app.state, "veterinary_agent_state")
    assert isinstance(state, VeterinaryAgentAppState)
    return state


def _request_for_app(app: FastAPI) -> Request:
    """构建绑定指定 FastAPI app 的测试 Request。

    :param app: FastAPI 应用实例。
    :return: 可传给依赖函数的 Request 对象。
    """

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
        }
    )


@contextmanager
def _observability_log_buffer() -> Iterator[BufferingHandler]:
    """临时挂载 Observability logger 的内存日志处理器。

    :return: 可读取 LogRecord buffer 的上下文迭代器。
    """

    logger = logging.getLogger("veterinary_agent.observability")
    old_level = logger.level
    old_disabled = logger.disabled
    handler = BufferingHandler(capacity=1024)
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.disabled = old_disabled


def _observability_log_payloads(
    records: Iterable[LogRecord],
) -> list[dict[str, object]]:
    """从日志记录中解析 Observability JSON 日志。

    :param records: Observability logger 产生的日志记录集合。
    :return: 已解析的 Observability 日志 payload 列表。
    """

    payloads: list[dict[str, object]] = []
    for record in records:
        if record.name != "veterinary_agent.observability":
            continue
        message = record.getMessage()
        parsed = json.loads(message)
        assert isinstance(parsed, dict)
        payloads.append(cast(dict[str, object], parsed))
    return payloads


def _find_payload_by_event(
    *,
    payloads: Iterable[dict[str, object]],
    event_name: str,
) -> dict[str, object]:
    """从日志 payload 集合中查找指定事件。

    :param payloads: Observability 日志 payload 集合。
    :param event_name: 需要查找的事件名称。
    :return: 匹配的日志 payload。
    :raises AssertionError: 当未找到指定事件时抛出。
    """

    for payload in payloads:
        if payload.get("event_name") == event_name:
            return payload
    raise AssertionError(f"未找到 Observability 日志事件: {event_name}")


def test_load_observability_settings_from_default_yaml() -> None:
    """验证 Observability 可从默认配置源加载。

    :return: None。
    """

    settings = load_observability_settings()

    assert settings.enabled is True
    assert settings.metrics.endpoint_path == "/metrics"
    assert "trace_id" in settings.label_policy.forbidden_metric_labels
    assert "user_id" in settings.label_policy.forbidden_metric_labels
    assert "endpoint" in settings.label_policy.allowed_metric_labels


def test_load_observability_settings_from_custom_yaml(tmp_path: Path) -> None:
    """验证 Observability 可从指定 YAML 文件加载。

    :param tmp_path: pytest 提供的临时目录。
    :return: None。
    """

    config_path = tmp_path / "observability.yaml"
    config_path.write_text(
        "\n".join(
            [
                "enabled: true",
                "config_version: observability.test",
                "metrics:",
                "  enabled: true",
                "  endpoint_enabled: true",
                "  endpoint_path: /internal/metrics",
                "  exclude_paths:",
                "    - /internal/metrics",
                "  duration_buckets_seconds:",
                "    - 0.1",
                "    - 1.0",
                "  max_label_value_length: 64",
                "logging:",
                "  enabled: true",
                "  level: WARNING",
                "  max_field_bytes: 1024",
                "tracing:",
                "  enabled: false",
                "  sample_rate: 0.0",
                "  service_name: veterinary-agent-test",
                "  environment: test",
                "  otlp_endpoint: null",
                "  exporter_timeout_seconds: 1.0",
                "label_policy:",
                "  allow_unlisted_labels: false",
                "  allowed_metric_labels:",
                "    - endpoint",
                "    - method",
                "    - status",
                "  forbidden_metric_labels:",
                "    - trace_id",
                "    - user_id",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_observability_settings(config_path)

    assert settings.config_version == "observability.test"
    assert settings.metrics.endpoint_path == "/internal/metrics"
    assert settings.logging.level == "WARNING"


@pytest.mark.parametrize(
    "buckets",
    [
        [1.0, 0.1],
        [0.1, 0.1],
        [0.0, 1.0],
    ],
)
def test_observability_metrics_config_rejects_invalid_buckets(
    buckets: list[float],
) -> None:
    """验证 metrics histogram 桶边界必须升序、去重且大于零。

    :param buckets: 测试用 histogram 桶边界。
    :return: None。
    """

    with pytest.raises(ValidationError):
        ObservabilityMetricsConfig(duration_buckets_seconds=buckets)


def test_observability_label_policy_rejects_overlapped_labels() -> None:
    """验证指标 label 白名单与禁止列表不得重叠。

    :return: None。
    """

    with pytest.raises(ValidationError):
        ObservabilityLabelPolicyConfig(
            allowed_metric_labels=["endpoint"],
            forbidden_metric_labels=["endpoint"],
        )


def test_observability_tracing_config_rejects_zero_sample_rate_when_enabled() -> None:
    """验证启用 tracing 时 sample_rate 必须大于零。

    :return: None。
    """

    with pytest.raises(ValidationError):
        ObservabilityTracingConfig(enabled=True, sample_rate=0.0)


def test_runtime_config_provider_reads_observability_namespace() -> None:
    """验证 RuntimeConfig provider 可读取 Observability 命名空间。

    :return: None。
    """

    observability_settings = ObservabilitySettings()
    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        observability_settings=observability_settings,
    )

    assert provider.get_namespace(RuntimeConfigNamespace.OBSERVABILITY) is (
        provider.current_snapshot().observability
    )
    assert provider.get_value(key="observability.metrics.endpoint_path") == "/metrics"
    assert "observability" in provider.trace_safe_summary()


def test_runtime_config_trace_safe_summary_hides_tracing_endpoint() -> None:
    """验证 RuntimeConfig trace-safe 摘要不暴露 OTLP endpoint。

    :return: None。
    """

    observability_settings = ObservabilitySettings(
        tracing=ObservabilityTracingConfig(
            enabled=False,
            sample_rate=0.0,
            service_name="veterinary-agent-test",
            environment="test",
            otlp_endpoint="http://collector.internal:4317",
            exporter_timeout_seconds=1.0,
        )
    )
    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        observability_settings=observability_settings,
    )
    summary_text = str(provider.trace_safe_summary())

    assert "otlp_endpoint" not in summary_text
    assert "collector.internal" not in summary_text


def test_observability_rejects_forbidden_metric_label() -> None:
    """验证 Observability 拒绝高基数或敏感指标 label。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())

    error = provider.record_metric(
        metric_name="custom_metric_total",
        value=1.0,
        metric_type=MetricType.COUNTER,
        labels={"trace_id": "trace_forbidden"},
    )

    assert error is not None
    assert error.code is ObservabilityErrorCode.OBS_LABEL_REJECTED


def test_observability_rejects_unlisted_metric_label() -> None:
    """验证 Observability 默认拒绝白名单外指标 label。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())

    error = provider.record_metric(
        metric_name="custom_metric_total",
        value=1.0,
        metric_type=MetricType.COUNTER,
        labels={"unknown_label": "value"},
    )

    assert error is not None
    assert error.code is ObservabilityErrorCode.OBS_LABEL_REJECTED


def test_observability_rejects_invalid_metric_name() -> None:
    """验证 Observability 拒绝非法指标名。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())

    error = provider.record_metric(
        metric_name="bad metric name",
        value=1.0,
        metric_type=MetricType.COUNTER,
        labels={"endpoint": "/agent/turns"},
    )

    assert error is not None
    assert error.code is ObservabilityErrorCode.OBS_METRIC_NAME_INVALID


def test_observability_rejects_sensitive_structured_event_field() -> None:
    """验证结构化事件拒绝疑似敏感字段名。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())

    error = provider.record_event(
        event_name="unsafe.event",
        component="ObservabilityTest",
        level=StructuredLogLevel.INFO,
        safe_fields={"prompt": "不要进入日志的完整 prompt"},
    )

    assert error is not None
    assert error.code is ObservabilityErrorCode.OBS_EVENT_UNSAFE


def test_observability_truncates_oversized_structured_event_field() -> None:
    """验证结构化事件超长字段会被裁剪为摘要。

    :return: None。
    """

    settings = ObservabilitySettings()
    settings.logging.max_field_bytes = 256
    provider = create_observability_provider(settings=settings)

    with _observability_log_buffer() as handler:
        error = provider.record_event(
            event_name="large.event",
            component="ObservabilityTest",
            safe_fields={"large_field": "x" * 1024},
        )

    payload = _find_payload_by_event(
        payloads=_observability_log_payloads(handler.buffer),
        event_name="large.event",
    )
    fields = payload.get("fields")
    assert error is None
    assert isinstance(fields, dict)
    large_field = fields.get("large_field")
    assert isinstance(large_field, dict)
    assert large_field["truncated"] is True


def test_observability_renders_prometheus_metrics() -> None:
    """验证 Observability 可渲染 Prometheus 文本指标。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())
    handle = provider.start_request(
        request_id="req_metrics",
        trace_id="trace_metrics",
        endpoint="/agent/turns",
        method="POST",
        streaming=False,
    )

    provider.finish_request(handle=handle, status_code=200)
    output = provider.render_prometheus_metrics()

    assert "# HELP http_requests_total HTTP 请求总数。" in output
    assert "# TYPE http_requests_total counter" in output
    assert 'endpoint="/agent/turns"' in output
    assert "http_request_duration_seconds_bucket" in output


def test_observability_excludes_configured_paths_from_http_metrics() -> None:
    """验证配置排除路径不会产生 HTTP 请求指标样本。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())
    handle = provider.start_request(
        request_id="req_metrics_excluded",
        trace_id="trace_metrics_excluded",
        endpoint="/metrics",
        method="GET",
        streaming=False,
    )

    provider.finish_request(handle=handle, status_code=200)
    output = provider.render_prometheus_metrics()

    assert 'endpoint="/metrics"' not in output


def test_observability_truncates_metric_label_value() -> None:
    """验证指标 label 值会按配置裁剪。

    :return: None。
    """

    settings = ObservabilitySettings(
        metrics=ObservabilityMetricsConfig(max_label_value_length=8)
    )
    provider = create_observability_provider(settings=settings)

    provider.record_metric(
        metric_name="custom_metric_total",
        value=1.0,
        metric_type=MetricType.COUNTER,
        labels={"endpoint": "/very/long/path"},
    )
    output = provider.render_prometheus_metrics()

    assert 'endpoint="/very/lo"' in output
    assert "/very/long/path" not in output


def test_observability_records_span_llm_tool_and_segment_metrics() -> None:
    """验证 span、LLM、tool 与 segment 摘要会进入技术指标。

    :return: None。
    """

    provider = create_observability_provider(settings=ObservabilitySettings())
    span_handle = provider.start_span(
        span_name="node.input_safety",
        component="GraphRuntime",
        safe_attributes={"node_name": "input_safety"},
    )

    provider.finish_span(handle=span_handle, status=SpanStatus.SUCCEEDED)
    provider.record_llm_call(
        agent_name="StandardConsultationAgent",
        generation_profile="standard",
        model_provider="test-provider",
        model_name="test-model",
        status="succeeded",
        duration_seconds=0.25,
        prompt_tokens=10,
        completion_tokens=20,
        retry_count=1,
    )
    provider.record_tool_call(
        tool_name="reference_range_lookup",
        status="succeeded",
        duration_seconds=0.1,
    )
    provider.record_segment_publish(
        segment_type="answer",
        generation_profile="standard",
        is_first_segment=True,
        status="completed",
        duration_seconds=0.05,
    )
    output = provider.render_prometheus_metrics()

    assert "technical_span_duration_seconds_bucket" in output
    assert "llm_calls_total" in output
    assert "llm_total_tokens_total" in output
    assert "tool_calls_total" in output
    assert "segments_published_total" in output
    assert "stream_first_byte_duration_seconds_bucket" in output


def test_observability_tracing_degrades_without_blocking() -> None:
    """验证 tracing backend 不可用时 Observability 不阻断主链路。

    :return: None。
    """

    settings = ObservabilitySettings(
        tracing=ObservabilityTracingConfig(
            enabled=True,
            sample_rate=1.0,
            service_name="veterinary-agent-test",
            environment="test",
            otlp_endpoint=None,
            exporter_timeout_seconds=1.0,
        )
    )
    provider = create_observability_provider(settings=settings)

    assert provider.is_ready() is True


def test_app_mounts_observability_provider_and_dependency() -> None:
    """验证 FastAPI lifespan 会装配 Observability provider 并可通过依赖读取。

    :return: None。
    """

    app = create_app(settings=_settings_without_orchestrator_readiness())

    with TestClient(app) as client:
        state = _state_from_app(cast(FastAPI, client.app))
        request = _request_for_app(cast(FastAPI, client.app))

        assert state.observability_provider is not None
        assert state.observability_ready is True
        assert get_observability_provider(request) is state.observability_provider


def test_app_exposes_metrics_endpoint() -> None:
    """验证 FastAPI 应用暴露 Observability metrics endpoint。

    :return: None。
    """

    with TestClient(
        create_app(settings=_settings_without_orchestrator_readiness())
    ) as client:
        response = client.get("/agent/turns")
        metrics_response = client.get("/metrics")

    assert response.status_code == 405
    assert response.headers["X-Process-Time-Ms"]
    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith(PROMETHEUS_CONTENT_TYPE)
    assert "http_requests_total" in metrics_response.text


def test_app_metrics_endpoint_can_be_disabled() -> None:
    """验证关闭 metrics endpoint 后 `/metrics` 返回明确错误状态。

    :return: None。
    """

    observability_settings = ObservabilitySettings()
    observability_settings.metrics.endpoint_enabled = False

    with TestClient(
        create_app(
            settings=_settings_without_orchestrator_readiness(),
            observability_settings=observability_settings,
        )
    ) as client:
        response = client.get("/metrics")

    assert response.status_code == 404
    assert response.json()["code"] == "OBS_METRICS_ENDPOINT_UNAVAILABLE"


def test_app_supports_custom_metrics_endpoint_path() -> None:
    """验证 FastAPI 应用支持自定义 metrics endpoint 路径。

    :return: None。
    """

    observability_settings = ObservabilitySettings(
        metrics=ObservabilityMetricsConfig(
            endpoint_path="/internal/metrics",
            exclude_paths=["/internal/metrics", "/health", "/ready"],
        )
    )

    with TestClient(
        create_app(
            settings=_settings_without_orchestrator_readiness(),
            observability_settings=observability_settings,
        )
    ) as client:
        default_response = client.get("/metrics")
        custom_response = client.get("/internal/metrics")

    assert default_response.status_code == 404
    assert custom_response.status_code == 200
    assert "http_requests_total" in custom_response.text


def test_api_ingress_binds_final_identity_to_observability_context() -> None:
    """验证 ApiIngress 解析出的最终 request_id 与 trace_id 会回填观测上下文。

    :return: None。
    """

    with _observability_log_buffer() as handler:
        with TestClient(
            create_app(settings=_settings_without_orchestrator_readiness())
        ) as client:
            response = client.post(
                "/agent/turns",
                headers={
                    "X-Request-ID": "req_obs_header",
                    "X-Trace-ID": "trace_obs_header",
                },
                json=_agent_turn_payload(
                    request_id="req_obs_header",
                    trace_id="trace_obs_header",
                ),
            )

    payload = _find_payload_by_event(
        payloads=reversed(_observability_log_payloads(handler.buffer)),
        event_name="http.request.finished",
    )

    assert response.status_code == 503
    assert response.json()["request_id"] == "req_obs_header"
    assert payload["request_id"] == "req_obs_header"
    assert payload["trace_id"] == "trace_obs_header"
