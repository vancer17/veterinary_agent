##################################################################################################
# 文件: tests/vet_output_safety_reviewer/test_dto_contract.py
# 作用: 验证 VetOutputSafetyReviewer DTO 的字段清理、关系约束和输出结果契约。
# 边界: 只校验组件公共 DTO，不执行审查服务、不调用 GuardrailFramework 或 trace 存储。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent.vet_output_safety_reviewer import (
    OutputFindingSeverity,
    OutputFindingType,
    OutputGuardActionDto,
    OutputReviewTracePatchDto,
    OutputSafetyFindingDto,
    OutputSafetyReviewRequestDto,
    OutputSafetyReviewResultDto,
    ReviewActionType,
    ReviewDomain,
    ReviewInputContextDto,
    ReviewStatus,
    RewritePlanDto,
)


def test_request_dto_strips_string_fields() -> None:
    """验证输出审查请求 DTO 会清理字符串字段首尾空白。

    :return: None。
    """

    request = OutputSafetyReviewRequestDto(
        request_id=" request-test ",
        trace_id=" trace-test ",
        run_id=" run-test ",
        session_id=" session-test ",
        user_id=" user-test ",
        current_pet_id=" pet-test ",
        task_id=" task-test ",
        segment_id=" segment-test ",
        generation_profile=" standard ",
        executor_key=" standard_consultation ",
        draft_response_ref=" draft-ref-test ",
        draft_response_text=" 草稿内容 ",
        input_context=ReviewInputContextDto(),
        params_version=" params-test ",
        config_snapshot_id=" config-test ",
    )

    assert request.request_id == "request-test"
    assert request.generation_profile == "standard"
    assert request.draft_response_text == "草稿内容"


def test_result_rejects_p0_finding_without_action() -> None:
    """验证 P0 候选发现项缺少护栏动作时会被 DTO 拒绝。

    :return: None。
    """

    finding = OutputSafetyFindingDto(
        finding_id="finding-p0-test",
        finding_type=OutputFindingType.ACUTE_WITHOUT_URGENT_CARE,
        severity=OutputFindingSeverity.CRITICAL,
        reason_code="OUTPUT_REVIEW_ACUTE_WITHOUT_URGENT_CARE",
        evidence_ref="draft-ref-test",
        source_review_domain=ReviewDomain.CLINICAL_SAFETY,
        p0_candidate=True,
    )

    with pytest.raises(ValidationError):
        OutputSafetyReviewResultDto(
            task_id="task-test",
            segment_id="segment-test",
            reviewed_draft_ref="reviewed-ref-test",
            reviewed_draft_text="审查后草稿",
            status=ReviewStatus.REVIEWED_WITH_REWRITE,
            findings=[finding],
            guard_actions=[],
            rewrite_plan=RewritePlanDto(
                plan_id="rewrite-plan-test",
                action_types=[ReviewActionType.REMOVE_SPAN],
                target_finding_ids=[finding.finding_id],
                fallback_recommended=False,
                required_constraints=[finding.reason_code],
            ),
            fallback_recommended=False,
            review_confidence=0.7,
            degraded_flags=[],
            trace_patch=OutputReviewTracePatchDto(
                reviewer_version="reviewer-test",
                writer_version="writer-test",
                finding_types=[finding.finding_type],
                action_types=[ReviewActionType.REMOVE_SPAN],
                degraded_flags=[],
                review_domains=[ReviewDomain.CLINICAL_SAFETY],
            ),
        )


def test_result_accepts_p0_finding_with_matching_action() -> None:
    """验证 P0 候选发现项被护栏动作覆盖时结果 DTO 可通过校验。

    :return: None。
    """

    finding = OutputSafetyFindingDto(
        finding_id="finding-p0-test",
        finding_type=OutputFindingType.ACUTE_WITHOUT_URGENT_CARE,
        severity=OutputFindingSeverity.CRITICAL,
        reason_code="OUTPUT_REVIEW_ACUTE_WITHOUT_URGENT_CARE",
        evidence_ref="draft-ref-test",
        source_review_domain=ReviewDomain.CLINICAL_SAFETY,
        p0_candidate=True,
    )
    action = OutputGuardActionDto(
        action_id="action-p0-test",
        action_type=ReviewActionType.REMOVE_SPAN,
        reason_code=finding.reason_code,
        before_ref="draft-ref-test",
        after_ref="reviewed-ref-test",
        source_finding_id=finding.finding_id,
    )

    result = OutputSafetyReviewResultDto(
        task_id="task-test",
        segment_id="segment-test",
        reviewed_draft_ref="reviewed-ref-test",
        reviewed_draft_text="审查后草稿",
        status=ReviewStatus.REVIEWED_WITH_REWRITE,
        findings=[finding],
        guard_actions=[action],
        rewrite_plan=RewritePlanDto(
            plan_id="rewrite-plan-test",
            action_types=[ReviewActionType.REMOVE_SPAN],
            target_finding_ids=[finding.finding_id],
            fallback_recommended=False,
            required_constraints=[finding.reason_code],
        ),
        fallback_recommended=False,
        review_confidence=0.7,
        degraded_flags=[],
        trace_patch=OutputReviewTracePatchDto(
            reviewer_version="reviewer-test",
            writer_version="writer-test",
            finding_types=[finding.finding_type],
            action_types=[action.action_type],
            degraded_flags=[],
            review_domains=[ReviewDomain.CLINICAL_SAFETY],
        ),
    )

    assert result.guard_actions[0].source_finding_id == finding.finding_id


def test_fallback_status_requires_fallback_flag() -> None:
    """验证 fallback 状态必须显式声明 fallback_recommended。

    :return: None。
    """

    with pytest.raises(ValidationError):
        OutputSafetyReviewResultDto(
            task_id="task-test",
            segment_id="segment-test",
            reviewed_draft_ref="reviewed-ref-test",
            reviewed_draft_text="审查后草稿",
            status=ReviewStatus.FALLBACK_RECOMMENDED,
            findings=[],
            guard_actions=[
                OutputGuardActionDto(
                    action_id="fallback-action-test",
                    action_type=ReviewActionType.FALLBACK_RECOMMENDED,
                    reason_code="OUTPUT_REVIEW_FALLBACK_RECOMMENDED",
                    before_ref="draft-ref-test",
                    after_ref="reviewed-ref-test",
                    source_finding_id=None,
                )
            ],
            rewrite_plan=RewritePlanDto(
                plan_id="rewrite-plan-test",
                action_types=[ReviewActionType.FALLBACK_RECOMMENDED],
                target_finding_ids=[],
                fallback_recommended=False,
                required_constraints=[],
            ),
            fallback_recommended=False,
            review_confidence=0.4,
            degraded_flags=[],
            trace_patch=OutputReviewTracePatchDto(
                reviewer_version="reviewer-test",
                writer_version="writer-test",
                finding_types=[],
                action_types=[ReviewActionType.FALLBACK_RECOMMENDED],
                degraded_flags=[],
                review_domains=[],
            ),
        )
