##################################################################################################
# 文件: tests/vet_output_safety_reviewer/test_ports.py
# 作用: 验证 VetOutputSafetyReviewer 的 TODO 端口和依赖缺失时的安全降级行为。
# 边界: 不实现真实 MedicationPolicy 或 LogicTraceStore，仅验证显式 TODO 空壳契约。
##################################################################################################

import asyncio

from veterinary_agent.vet_output_safety_reviewer import (
    MedicationPolicyAnalysisRequestDto,
    MedicationPolicyDecisionStatus,
    OutputReviewTraceWriteStatus,
    ReviewStatus,
    TODO_MEDICATION_POLICY_ERROR_CODE,
    TodoMedicationPolicyPort,
)

from .helpers import (
    build_output_review_request,
    build_provider,
    build_reviewer,
)


def test_todo_medication_policy_port_returns_unavailable_decision() -> None:
    """验证 TODO 用药策略端口会返回显式不可用判定。

    :return: None。
    """

    port = TodoMedicationPolicyPort()
    request = MedicationPolicyAnalysisRequestDto(
        request_id="request-med-test",
        trace_id="trace-med-test",
        candidate_text_ref="draft-ref-test",
        candidate_text="请给宠物口服药物。",
        generation_profile="standard",
        executor_key="standard_consultation",
        pet_species="dog",
        span_candidates=[],
        text_source="draft_response",
        params_version="params-test",
    )

    decision = asyncio.run(port.analyze_medication_expression(request))

    assert port.is_ready() is False
    assert decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
    assert decision.fallback_required is True
    assert TODO_MEDICATION_POLICY_ERROR_CODE in decision.degraded_flags


def test_default_trace_sink_marks_service_result_degraded() -> None:
    """验证默认 TODO trace sink 会让服务结果显式暴露 trace 降级。

    :return: None。
    """

    provider = build_provider()
    reviewer = build_reviewer(provider=provider)
    request = build_output_review_request(
        provider=provider,
        draft_text="这是普通的日常护理建议。",
        generation_profile="nonmedical",
        medical_content_expected=False,
    )

    result = asyncio.run(reviewer.review_draft_response_safety(request))

    assert result.status is ReviewStatus.REVIEWED_READY
    assert result.trace_delivery_status is OutputReviewTraceWriteStatus.DEGRADED
    assert "trace_write_degraded" in result.degraded_flags
