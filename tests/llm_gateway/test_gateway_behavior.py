##################################################################################################
# 文件: tests/llm_gateway/test_gateway_behavior.py
# 作用: 验证 DefaultLlmGateway 非流式调用、profile 降级、流式事件、错误归一与摘要留痕。
# 边界: 使用 fake ProviderAdapter，不访问真实模型代理，不实现 AgentRunner 或业务安全护栏。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.llm_gateway import (
    LlmGatewayError,
    LlmGatewayErrorCode,
    LlmGatewayOperation,
    LlmFinishReason,
    LlmProviderRouteHealthDto,
    LlmStreamEventType,
    LlmTraceWriteStatus,
    LlmUsageSummaryDto,
    ProviderStreamEventDto,
    ProviderStreamEventType,
    create_default_llm_gateway,
)

from . import (
    FakeProviderAdapter,
    RecordingTraceStore,
    build_invocation_request,
    build_success_response,
    build_test_settings,
    collect_stream_events,
)


def test_invoke_uses_model_alias_and_writes_trace_summary() -> None:
    """验证非流式调用使用路由模型别名并写入脱敏调用摘要。

    :return: None。
    """

    adapter = FakeProviderAdapter(response=build_success_response())
    trace_store = RecordingTraceStore()
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
        trace_store=trace_store,
        config_snapshot_id="snapshot_1",
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert result.content == "ok"
    assert result.trace_write_status is LlmTraceWriteStatus.DELIVERED
    assert adapter.invoke_requests[0].model_alias == "test-primary"
    assert trace_store.summaries[0].call_id == result.call_id
    assert trace_store.summaries[0].config_snapshot_id == "snapshot_1"


def test_invoke_falls_back_to_compatible_profile() -> None:
    """验证首选 profile 失败后可降级到能力兼容的备用 profile。

    :return: None。
    """

    primary = FakeProviderAdapter(
        error=LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="provider unavailable",
        )
    )
    fallback = FakeProviderAdapter(
        response=build_success_response(
            content="fallback ok",
            actual_model="test-fallback",
        )
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(include_fallback=True),
        adapters_by_profile={
            "profile_primary": primary,
            "profile_fallback": fallback,
        },
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert result.content == "fallback ok"
    assert result.fallback_used is True
    assert result.fallback_chain == ["profile_primary", "profile_fallback"]
    assert primary.invoke_requests
    assert fallback.invoke_requests[0].model_alias == "test-fallback"


def test_invoke_retries_same_profile_and_reports_retry_count() -> None:
    """验证可重试错误会在同一 profile 内有限重试并记录次数。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        invoke_outcomes=[
            LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="temporary provider failure",
            ),
            build_success_response(content="retry recovered"),
        ]
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(retry_max_attempts=2),
        adapters_by_profile={"profile_primary": adapter},
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert result.content == "retry recovered"
    assert result.retry_count == 1
    assert len(adapter.invoke_requests) == 2


def test_invoke_reports_retry_exhausted_after_multiple_attempts() -> None:
    """验证同一 profile 多次物理调用失败后返回重试耗尽错误。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        error=LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="provider unavailable",
        )
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(retry_max_attempts=2),
        adapters_by_profile={"profile_primary": adapter},
    )

    with pytest.raises(LlmGatewayError) as exc_info:
        asyncio.run(gateway.invoke(build_invocation_request()))

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_RETRY_EXHAUSTED
    assert len(adapter.invoke_requests) == 2


def test_invoke_preserves_single_non_retryable_error() -> None:
    """验证单次不可重试响应错误保留原始标准错误码。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        error=LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="malformed response",
        )
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(retry_max_attempts=3),
        adapters_by_profile={"profile_primary": adapter},
    )

    with pytest.raises(LlmGatewayError) as exc_info:
        asyncio.run(gateway.invoke(build_invocation_request()))

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE
    assert len(adapter.invoke_requests) == 1


def test_unavailable_fallback_profile_does_not_block_primary_profile() -> None:
    """验证无关备用 profile 不可用时，健康的首选 profile 仍可执行。

    :return: None。
    """

    primary = FakeProviderAdapter(response=build_success_response())
    fallback = FakeProviderAdapter(ready=False)
    gateway = create_default_llm_gateway(
        settings=build_test_settings(include_fallback=True),
        adapters_by_profile={
            "profile_primary": primary,
            "profile_fallback": fallback,
        },
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert gateway.is_ready() is True
    assert result.actual_profile_id == "profile_primary"
    assert result.fallback_used is False


def test_stream_yields_delta_usage_and_completed_event() -> None:
    """验证流式调用归一化文本增量、usage 和完成事件。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        stream_events=[
            ProviderStreamEventDto(
                event_type=ProviderStreamEventType.DELTA,
                actual_model="test-primary",
                delta="先观察",
            ),
            ProviderStreamEventDto(
                event_type=ProviderStreamEventType.USAGE,
                actual_model="test-primary",
                usage=LlmUsageSummaryDto(
                    input_tokens=9,
                    output_tokens=2,
                    total_tokens=11,
                ),
            ),
            ProviderStreamEventDto(
                event_type=ProviderStreamEventType.COMPLETED,
                actual_model="test-primary",
                finish_reason=LlmFinishReason.STOP,
            ),
        ]
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    events = asyncio.run(
        collect_stream_events(gateway.stream(build_invocation_request(stream=True)))
    )

    assert [event.event_type for event in events] == [
        LlmStreamEventType.STARTED,
        LlmStreamEventType.DELTA,
        LlmStreamEventType.USAGE,
        LlmStreamEventType.COMPLETED,
    ]
    assert events[1].delta == "先观察"
    assert events[-1].finish_reason == "stop"
    assert events[-1].usage is not None
    assert events[-1].usage.total_tokens == 11


def test_stream_error_after_delta_returns_error_without_retry() -> None:
    """验证首个有效流式事件后出错会返回错误事件并停止，不透明重放。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        stream_events=[
            ProviderStreamEventDto(
                event_type=ProviderStreamEventType.DELTA,
                actual_model="test-primary",
                delta="已开始",
            )
        ],
        stream_error=LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE,
            operation=LlmGatewayOperation.STREAM_LLM,
            message="provider stream failed",
        ),
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    events = asyncio.run(
        collect_stream_events(gateway.stream(build_invocation_request(stream=True)))
    )

    assert [event.event_type for event in events] == [
        LlmStreamEventType.STARTED,
        LlmStreamEventType.DELTA,
        LlmStreamEventType.ERROR,
    ]
    assert len(adapter.stream_requests) == 1
    assert events[-1].normalized_error is not None
    assert events[-1].normalized_error.code is (
        LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE
    )


def test_stream_first_token_timeout_preserves_specific_error_code() -> None:
    """验证首个有效事件超时返回专用错误且不会伪造流式增量。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        stream_initial_delay_seconds=0.05,
        stream_events=[
            ProviderStreamEventDto(
                event_type=ProviderStreamEventType.DELTA,
                actual_model="test-primary",
                delta="too late",
            )
        ],
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(first_token_timeout_seconds=0.01),
        adapters_by_profile={"profile_primary": adapter},
    )

    events = asyncio.run(
        collect_stream_events(gateway.stream(build_invocation_request(stream=True)))
    )

    assert [event.event_type for event in events] == [
        LlmStreamEventType.STARTED,
        LlmStreamEventType.ERROR,
    ]
    assert events[-1].normalized_error is not None
    assert (
        events[-1].normalized_error.code is LlmGatewayErrorCode.LLM_FIRST_TOKEN_TIMEOUT
    )


def test_gateway_healthcheck_and_close_shared_adapter_once() -> None:
    """验证路由健康检查透传结果，关闭时共享适配器只释放一次。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        response=build_success_response(),
        health_result=LlmProviderRouteHealthDto(
            provider_route_id="route_primary",
            healthy=True,
            latency_ms=3,
        ),
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(include_fallback=True),
        adapters_by_profile={
            "profile_primary": adapter,
            "profile_fallback": adapter,
        },
    )

    health = asyncio.run(gateway.check_provider_route_health("route_primary"))
    asyncio.run(gateway.close())
    asyncio.run(gateway.close())

    assert health.healthy is True
    assert health.latency_ms == 3
    assert adapter.close_calls == 1
    assert gateway.is_ready() is False


def test_gateway_rejects_mismatched_stream_entrypoint() -> None:
    """验证非流式入口拒绝 stream=true 请求。

    :return: None。
    """

    adapter = FakeProviderAdapter(response=build_success_response())
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    with pytest.raises(LlmGatewayError) as exc_info:
        asyncio.run(gateway.invoke(build_invocation_request(stream=True)))

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_INVALID_REQUEST
