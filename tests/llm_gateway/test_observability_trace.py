##################################################################################################
# 文件: tests/llm_gateway/test_observability_trace.py
# 作用: 验证 LlmGateway 指标、敏感字段边界、调用摘要写入、TracePolicy 与 TODO 留痕降级。
# 边界: 使用应用内 Observability 与测试 trace store；不接入真实 LogicTraceStore 或外部监控后端。
##################################################################################################

import asyncio
from typing import cast

from veterinary_agent import (
    LlmTracePolicyConfig,
    LlmTraceWriteStatus,
    ObservabilityProvider,
    ObservabilitySettings,
    TodoLlmCallTraceStore,
    create_default_llm_gateway,
    create_observability_provider,
)

from . import (
    FakeProviderAdapter,
    RaisingTraceStore,
    RecordingTraceStore,
    build_invocation_request,
    build_success_response,
    build_test_settings,
)


class RaisingObservabilityProvider:
    """LlmGateway 异常隔离测试使用的 Observability 替身。"""

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
        """在记录模型摘要时固定抛出异常。

        :param agent_name: 调用模型的 Agent 名称。
        :param generation_profile: 本次生成剖面。
        :param model_provider: 模型供应商标识。
        :param model_name: 模型标识。
        :param status: 调用状态。
        :param duration_seconds: 调用耗时。
        :param prompt_tokens: 输入 token 数。
        :param completion_tokens: 输出 token 数。
        :param retry_count: 重试次数。
        :param error_type: 可选错误类型。
        :return: 本方法不会正常返回。
        :raises RuntimeError: 固定模拟 Observability 不可用。
        """

        del (
            agent_name,
            generation_profile,
            model_provider,
            model_name,
            status,
            duration_seconds,
            prompt_tokens,
            completion_tokens,
            retry_count,
            error_type,
        )
        raise RuntimeError("observability unavailable")


def test_gateway_records_llm_metrics_without_request_identifiers() -> None:
    """验证模型调用指标包含低基数标签且不泄漏请求关联 ID。

    :return: None。
    """

    observability = create_observability_provider(
        settings=ObservabilitySettings(),
    )
    adapter = FakeProviderAdapter(response=build_success_response())
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
        observability_provider=observability,
    )
    request = build_invocation_request(
        call_id="llm_sensitive",
        metadata={"generation_profile": "standard"},
    ).model_copy(
        update={
            "trace_id": "trace_sensitive",
            "request_id": "req_sensitive",
        }
    )

    asyncio.run(gateway.invoke(request))
    output = observability.render_prometheus_metrics()

    assert "llm_calls_total" in output
    assert 'generation_profile="standard"' in output
    assert "llm_sensitive" not in output
    assert "trace_sensitive" not in output
    assert "req_sensitive" not in output


def test_observability_exception_does_not_change_model_result() -> None:
    """验证 Observability 异常不会将成功模型调用反向转换为失败。

    :return: None。
    """

    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={
            "profile_primary": FakeProviderAdapter(response=build_success_response())
        },
        observability_provider=cast(
            ObservabilityProvider,
            RaisingObservabilityProvider(),
        ),
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert result.content == "ok"


def test_trace_store_exception_degrades_result_and_records_metric() -> None:
    """验证模型调用摘要存储异常不会阻断成功结果，并记录留痕降级指标。

    :return: None。
    """

    observability = create_observability_provider(
        settings=ObservabilitySettings(),
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={
            "profile_primary": FakeProviderAdapter(response=build_success_response())
        },
        observability_provider=observability,
        trace_store=RaisingTraceStore(exception=RuntimeError("trace failed")),
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))
    output = observability.render_prometheus_metrics()

    assert result.trace_write_status is LlmTraceWriteStatus.DEGRADED
    assert "llm_gateway_trace_degraded_total" in output


def test_trace_policy_can_skip_call_summary_without_hanging_configuration() -> None:
    """验证 profile TracePolicy 关闭时跳过摘要写入且不影响模型调用。

    :return: None。
    """

    settings = build_test_settings()
    profile = settings.model_profiles[0].model_copy(
        update={"trace_policy": LlmTracePolicyConfig(emit_logic_trace_summary=False)}
    )
    settings = settings.model_copy(update={"model_profiles": [profile]})
    trace_store = RecordingTraceStore()
    gateway = create_default_llm_gateway(
        settings=settings,
        adapters_by_profile={
            "profile_primary": FakeProviderAdapter(response=build_success_response())
        },
        trace_store=trace_store,
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert result.trace_write_status is LlmTraceWriteStatus.SKIPPED
    assert trace_store.summaries == []


def test_todo_trace_store_returns_explicit_degraded_status() -> None:
    """验证其他领域尚未接入时 TODO 留痕空壳显式返回降级状态。

    :return: None。
    """

    trace_store = TodoLlmCallTraceStore()
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={
            "profile_primary": FakeProviderAdapter(response=build_success_response())
        },
        trace_store=trace_store,
    )

    result = asyncio.run(gateway.invoke(build_invocation_request()))

    assert trace_store.is_ready() is False
    assert result.trace_write_status is LlmTraceWriteStatus.DEGRADED


def test_call_summary_contains_only_normalized_metadata() -> None:
    """验证调用摘要不保存 prompt、completion 或业务 metadata 正文。

    :return: None。
    """

    trace_store = RecordingTraceStore()
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={
            "profile_primary": FakeProviderAdapter(
                response=build_success_response(content="sensitive completion")
            )
        },
        trace_store=trace_store,
    )

    asyncio.run(
        gateway.invoke(
            build_invocation_request(
                content="sensitive prompt",
                metadata={
                    "generation_profile": "standard",
                    "pet_note": "sensitive metadata",
                },
            )
        )
    )
    serialized_summary = trace_store.summaries[0].model_dump_json()

    assert "sensitive prompt" not in serialized_summary
    assert "sensitive completion" not in serialized_summary
    assert "sensitive metadata" not in serialized_summary
