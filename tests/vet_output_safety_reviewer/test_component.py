##################################################################################################
# 文件: tests/vet_output_safety_reviewer/test_component.py
# 作用: 验证 VetOutputSafetyReviewer 默认服务、Guardrail handler 适配与失败路径。
# 边界: 只用测试替身和公共包出口验证契约，不连接真实 trace 存储、外部模型或业务发布链路。
##################################################################################################

import asyncio

from veterinary_agent.guardrail_framework import (
    GuardrailFrameworkErrorCode,
    GuardrailStatus,
)
from veterinary_agent.vet_output_safety_reviewer import (
    OutputFindingType,
    OutputReviewTraceWriteStatus,
    ReviewStatus,
    VetOutputSafetyReviewerErrorCode,
    create_vet_output_safety_reviewer_guardrail_handler,
)

from .helpers import (
    RecordingOutputReviewTraceSink,
    build_guardrail_request,
    build_output_review_request,
    build_post_generation_policy,
    build_provider,
    build_reviewer,
)


def test_service_reviews_acute_draft_and_records_trace() -> None:
    """验证默认服务会对急症草稿执行受控改写并写入 trace。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingOutputReviewTraceSink()
    reviewer = build_reviewer(provider=provider, trace_sink=trace_sink)
    request = build_output_review_request(
        provider=provider,
        draft_text="如果出现呼吸困难，可以先观察几天。",
        signal_codes=["SAF-03"],
    )

    result = asyncio.run(reviewer.review_draft_response_safety(request))

    assert result.status is ReviewStatus.REVIEWED_WITH_REWRITE
    assert result.trace_delivery_status is OutputReviewTraceWriteStatus.RECORDED
    assert "立即就医" in result.reviewed_draft_text
    assert "辅助参考" in result.reviewed_draft_text
    assert any(
        finding.finding_type is OutputFindingType.ACUTE_WITHOUT_URGENT_CARE
        for finding in result.findings
    )
    assert any(
        finding.finding_type is OutputFindingType.DELAYED_CARE_RISK
        for finding in result.findings
    )
    assert len(trace_sink.records) == 1
    assert trace_sink.records[0].result.reviewed_draft_ref == result.reviewed_draft_ref


def test_handler_maps_review_result_to_guardrail_result() -> None:
    """验证 Guardrail handler 会把输出审查结果映射为护栏结果。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingOutputReviewTraceSink()
    reviewer = build_reviewer(provider=provider, trace_sink=trace_sink)
    handler = create_vet_output_safety_reviewer_guardrail_handler(reviewer=reviewer)
    policy = build_post_generation_policy(provider)
    request = build_guardrail_request(
        provider=provider,
        draft_text="如果出现呼吸困难，可以先观察几天。",
        signal_codes=["SAF-03"],
    )

    result = asyncio.run(handler.run_guardrail(policy=policy, request=request))

    assert result.status is GuardrailStatus.REWRITTEN
    assert result.publish_allowed is False
    assert result.reviewed_text_ref is not None
    assert result.findings
    assert result.actions
    assert result.metadata["review_status"] == ReviewStatus.REVIEWED_WITH_REWRITE.value
    assert result.trace_degraded is False


def test_handler_rejects_missing_executor_key_as_failed_result() -> None:
    """验证缺少 executor_key 时 handler 会返回明确失败结果。

    :return: None。
    """

    provider = build_provider()
    reviewer = build_reviewer(
        provider=provider,
        trace_sink=RecordingOutputReviewTraceSink(),
    )
    handler = create_vet_output_safety_reviewer_guardrail_handler(reviewer=reviewer)
    policy = build_post_generation_policy(provider)
    request = build_guardrail_request(
        provider=provider,
        draft_text="如果出现呼吸困难，可以先观察几天。",
        signal_codes=["SAF-03"],
    )
    request.task_input.pop("executor_key")
    raw_input_context = request.task_input["input_context"]
    assert isinstance(raw_input_context, dict)
    raw_input_context.pop("executor_key")

    result = asyncio.run(handler.run_guardrail(policy=policy, request=request))

    assert result.status is GuardrailStatus.FAILED
    assert result.error_code is GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR
    assert result.findings[0].reason_code == (
        VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_ASSESSMENT_MISSING.value
    )
