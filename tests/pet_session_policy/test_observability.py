##################################################################################################
# 文件: tests/pet_session_policy/test_observability.py
# 作用: 验证 PetSessionPolicy 指标、结构化事件、敏感字段边界与 Observability 异常隔离。
# 边界: 使用 Observability 公共 provider 或测试替身；不启动 metrics HTTP 端点或外部 exporter。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.config import ObservabilitySettings
from veterinary_agent.conversation_store import EnsureSessionResultDto
from veterinary_agent.observability import (
    MetricType,
    StructuredLogLevel,
    create_observability_provider,
)
from veterinary_agent.pet_session_policy import (
    PetSessionDecision,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
)

from .helpers import (
    FakeConversationStore,
    RaisingObservabilityProvider,
    RecordingObservabilityProvider,
    RecordingTraceSink,
    build_policy,
    build_request,
    build_session,
)


def test_policy_records_prometheus_metrics_with_low_cardinality_labels() -> None:
    """验证真实 Observability provider 记录策略总数与耗时指标。

    :return: None。
    """

    observability = create_observability_provider(
        settings=ObservabilitySettings(),
    )
    policy = build_policy(
        store=FakeConversationStore(
            result=EnsureSessionResultDto(
                session=build_session(
                    session_id="session_sensitive",
                    user_id="user_sensitive",
                    pet_id="pet_sensitive",
                ),
                created_new=True,
            )
        ),
        trace_sink=RecordingTraceSink(),
        observability_provider=observability,
    )

    asyncio.run(
        policy.ensure_context(
            build_request(
                request_id="req_sensitive",
                trace_id="trace_sensitive",
                user_id="user_sensitive",
                session_id="session_sensitive",
                pet_id="pet_sensitive",
            )
        )
    )
    output = observability.render_prometheus_metrics()

    assert "# TYPE pet_session_policy_total counter" in output
    assert "# TYPE pet_session_policy_duration_seconds histogram" in output
    assert 'component="pet_session_policy"' in output
    assert 'status="ALLOW_NEW_SESSION_BOUND"' in output
    assert "req_sensitive" not in output
    assert "trace_sensitive" not in output
    assert "user_sensitive" not in output
    assert "session_sensitive" not in output
    assert "pet_sensitive" not in output


def test_allow_decision_records_info_event_and_two_metrics() -> None:
    """验证允许判定记录 INFO 事件、总数指标与耗时指标。

    :return: None。
    """

    observability = RecordingObservabilityProvider()
    policy = build_policy(
        store=FakeConversationStore(
            result=EnsureSessionResultDto(
                session=build_session(),
                created_new=False,
            )
        ),
        trace_sink=RecordingTraceSink(),
        observability_provider=observability,
    )

    asyncio.run(policy.ensure_context(build_request()))

    assert [metric["metric_name"] for metric in observability.metrics] == [
        "pet_session_policy_total",
        "pet_session_policy_duration_seconds",
    ]
    assert observability.metrics[0]["metric_type"] is MetricType.COUNTER
    assert observability.metrics[1]["metric_type"] is MetricType.HISTOGRAM
    assert observability.events[0]["event_name"] == "pet_session_policy.finished"
    assert observability.events[0]["level"] is StructuredLogLevel.INFO
    safe_fields = observability.events[0]["safe_fields"]
    assert isinstance(safe_fields, dict)
    assert safe_fields["decision"] == PetSessionDecision.ALLOW_EXISTING_SESSION.value


def test_block_decision_records_warning_without_sensitive_fields() -> None:
    """验证阻断判定记录 WARNING 事件且不包含请求身份锚点。

    :return: None。
    """

    observability = RecordingObservabilityProvider()
    policy = build_policy(
        store=FakeConversationStore(),
        trace_sink=RecordingTraceSink(),
        observability_provider=observability,
    )

    with pytest.raises(PetSessionPolicyError):
        asyncio.run(
            policy.ensure_context(
                build_request(
                    request_id="req_secret",
                    trace_id="trace_secret",
                    user_id="user_secret",
                    session_id="session_secret",
                    pet_id=None,
                )
            )
        )

    event = observability.events[0]
    serialized_event = repr(event)
    assert event["level"] is StructuredLogLevel.WARNING
    assert "req_secret" not in serialized_event
    assert "trace_secret" not in serialized_event
    assert "user_secret" not in serialized_event
    assert "session_secret" not in serialized_event
    metric_labels = observability.metrics[0]["labels"]
    assert isinstance(metric_labels, dict)
    assert set(metric_labels) == {"component", "status"}


def test_observability_exception_does_not_change_allow_decision() -> None:
    """验证 Observability 异常不改变允许继续结果。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(
            result=EnsureSessionResultDto(
                session=build_session(),
                created_new=True,
            )
        ),
        trace_sink=RecordingTraceSink(),
        observability_provider=RaisingObservabilityProvider(),
    )

    context = asyncio.run(policy.ensure_context(build_request()))

    assert context.decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND


def test_observability_exception_does_not_change_block_error() -> None:
    """验证 Observability 异常不改变原始阻断错误。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(),
        trace_sink=RecordingTraceSink(),
        observability_provider=RaisingObservabilityProvider(),
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request(pet_id=None)))

    assert exc_info.value.code is PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING
