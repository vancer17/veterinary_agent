##################################################################################################
# 文件: tests/guardrail_framework/test_service.py
# 作用: 验证 GuardrailFramework 默认 service 的阶段执行、动作聚合、失败策略和发布许可边界。
# 边界: 使用测试 handler、内存 registry 与 fallback provider，不实现任何 L2 兽医业务规则。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.guardrail_framework import (
    GuardActionType,
    GuardrailFrameworkError,
    GuardrailFrameworkErrorCode,
    GuardrailPolicyDto,
    GuardrailRunResultDto,
    GuardrailStage,
    GuardrailStatus,
    InMemoryGuardrailHandlerRegistry,
    InMemoryGuardrailPolicyRegistry,
    create_default_guardrail_framework,
)

from .helpers import (
    InvalidOutputGuardrailHandler,
    RecordingFallbackTemplateProvider,
    RecordingGuardrailTraceSink,
    StaticGuardrailHandler,
    TimeoutGuardrailHandler,
    UnavailableFallbackTemplateProvider,
    action_types,
    build_framework_with_handler,
    build_provider,
    build_request,
    build_settings,
    custom_action,
    reason_codes,
)


def test_deterministic_gate_allow_result_becomes_publishable() -> None:
    """验证 deterministic gate 明确允许时框架产出发布许可。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingGuardrailTraceSink()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.DETERMINISTIC_GATE,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)
        ),
        trace_sink=trace_sink,
    )

    result = asyncio.run(
        framework.run_deterministic_gate(
            build_request(stage=GuardrailStage.DETERMINISTIC_GATE, provider=provider)
        )
    )

    assert result.status is GuardrailStatus.ALLOWED
    assert result.publish_allowed is True
    assert result.trace_degraded is False
    assert len(trace_sink.records) == 1
    assert trace_sink.records[0].result.publish_allowed is True


def test_non_gate_allow_result_is_not_publishable() -> None:
    """验证非 deterministic gate 阶段即使允许也不会产生发布许可。

    :return: None。
    """

    provider = build_provider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)
        ),
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.ALLOWED
    assert result.publish_allowed is False
    assert action_types(result) == [GuardActionType.ALLOW]


def test_rewrite_result_is_intermediate_and_not_publishable() -> None:
    """验证 rewrite 结果保留审查后引用但不能直接发布。

    :return: None。
    """

    provider = build_provider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(
                status=GuardrailStatus.REWRITTEN,
                reviewed_text_ref="reviewed-ref-test",
                actions=[custom_action()],
            )
        ),
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.REWRITTEN
    assert result.reviewed_text_ref == "reviewed-ref-test"
    assert result.publish_allowed is False
    assert result.actions[0].policy_id == "guardrail.post_generation_review.default"
    assert result.actions[0].action_type is GuardActionType.REWRITE


def test_block_result_has_priority_over_allow_result() -> None:
    """验证多个策略聚合时 block 优先于 allow。

    :return: None。
    """

    provider = build_provider()
    allow_policy = GuardrailPolicyDto(
        policy_id="guardrail.allow.test",
        policy_version="guardrail-policy.test",
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler_ref="allow_handler",
    )
    block_policy = GuardrailPolicyDto(
        policy_id="guardrail.block.test",
        policy_version="guardrail-policy.test",
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler_ref="block_handler",
    )
    policy_registry = InMemoryGuardrailPolicyRegistry([allow_policy, block_policy])
    handler_registry = InMemoryGuardrailHandlerRegistry(
        {
            "allow_handler": StaticGuardrailHandler(
                GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)
            ),
            "block_handler": StaticGuardrailHandler(
                GuardrailRunResultDto(status=GuardrailStatus.BLOCKED)
            ),
        }
    )
    framework = create_default_guardrail_framework(
        runtime_config_provider=provider,
        policy_registry=policy_registry,
        handler_registry=handler_registry,
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.BLOCKED
    assert result.publish_allowed is False
    assert action_types(result) == [GuardActionType.ALLOW, GuardActionType.BLOCK]


def test_failure_policy_can_return_fallback_without_publish_permission() -> None:
    """验证 fallback 失败策略返回带模板版本的中间结果且不允许发布。

    :return: None。
    """

    settings = build_settings(
        post_failure_strategy="fallback",
        post_fallback_template_ref="fallback.template.test",
    )
    provider = build_provider(settings=settings)
    fallback_provider = RecordingFallbackTemplateProvider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(
                status=GuardrailStatus.FAILED,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
            )
        ),
        fallback_provider=fallback_provider,
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.FALLBACK
    assert result.publish_allowed is False
    assert result.fallback_triggered is True
    assert result.fallback_template_version == "fallback-template.v-test"
    assert result.final_text_ref == "fallback-text-ref-test"
    assert len(fallback_provider.calls) == 1
    assert GuardActionType.FALLBACK in action_types(result)


def test_unavailable_fallback_template_blocks_result() -> None:
    """验证 fallback 模板不可用时框架返回阻断结果。

    :return: None。
    """

    settings = build_settings(
        post_failure_strategy="fallback",
        post_fallback_template_ref="fallback.template.test",
    )
    provider = build_provider(settings=settings)
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(
                status=GuardrailStatus.FAILED,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
            )
        ),
        fallback_provider=UnavailableFallbackTemplateProvider(),
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.BLOCKED
    assert result.publish_allowed is False
    assert (
        result.error_code
        is GuardrailFrameworkErrorCode.GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE
    )
    assert "GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE" in reason_codes(result.findings)


def test_missing_handler_is_mapped_to_fail_closed_block() -> None:
    """验证策略存在但 handler 未注册时默认 fail-closed。

    :return: None。
    """

    provider = build_provider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=None,
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.BLOCKED
    assert (
        result.error_code
        is GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_NOT_REGISTERED
    )
    assert result.publish_allowed is False


def test_timeout_handler_retries_limited_times_then_blocks() -> None:
    """验证 handler 超时只按配置进行有限重试并最终阻断。

    :return: None。
    """

    settings = build_settings(
        post_max_attempts=2,
        post_retry_on_timeout=True,
        post_handler_timeout_seconds=0.01,
        post_stage_timeout_seconds=0.03,
    )
    provider = build_provider(settings=settings)
    handler = TimeoutGuardrailHandler(sleep_seconds=0.05)
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=handler,
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert handler.call_count == 2
    assert result.status is GuardrailStatus.BLOCKED
    assert result.error_code is GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_TIMEOUT


def test_invalid_handler_output_is_schema_invalid_and_blocked() -> None:
    """验证 handler 非法输出不会被静默视为通过。

    :return: None。
    """

    provider = build_provider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        handler=InvalidOutputGuardrailHandler(),
    )

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.BLOCKED
    assert (
        result.error_code is GuardrailFrameworkErrorCode.GUARDRAIL_OUTPUT_SCHEMA_INVALID
    )


def test_stage_specific_entry_rejects_mismatched_stage() -> None:
    """验证阶段专用入口拒绝 stage 不匹配请求。

    :return: None。
    """

    provider = build_provider()
    framework = create_default_guardrail_framework(runtime_config_provider=provider)

    with pytest.raises(GuardrailFrameworkError) as exc_info:
        asyncio.run(
            framework.run_post_generation_review(
                build_request(
                    stage=GuardrailStage.DETERMINISTIC_GATE,
                    provider=provider,
                )
            )
        )

    assert exc_info.value.code is GuardrailFrameworkErrorCode.GUARDRAIL_STAGE_MISMATCH
