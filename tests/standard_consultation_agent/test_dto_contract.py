##################################################################################################
# 文件: tests/standard_consultation_agent/test_dto_contract.py
# 作用: 验证 StandardConsultationAgent DTO 的问题预算、安全升级和错误 DTO 契约。
# 边界: 只构造进程内 DTO，不调用 service、AgentRunner、RAG、Trace 存储或 GraphRuntime。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent.standard_consultation_agent import (
    CandidateQuestionDto,
    ConsultationLayer,
    DraftStatus,
    EscalationRequestDto,
    QuestionPurpose,
    RiskImpact,
    StandardConsultationDraftDto,
    StandardConsultationErrorCode,
    StandardConsultationOperation,
    StandardTracePatchDto,
    build_standard_consultation_error_dto,
)


def _build_trace_patch() -> StandardTracePatchDto:
    """构建测试使用的标准问诊 trace patch。

    :return: 可放入草稿 DTO 的 trace patch。
    """

    return StandardTracePatchDto(
        standard_agent_version="standard-consultation-agent.v1",
        orchestrator_version="standard-consultation-orchestrator.v1",
        layer_before=ConsultationLayer.L0_COLLECTION,
        layer_after=ConsultationLayer.L1_TRIAGE,
    )


def _build_question(index: int) -> CandidateQuestionDto:
    """构建测试使用的候选问题。

    :param index: 问题序号。
    :return: 标准候选问题 DTO。
    """

    return CandidateQuestionDto(
        question_id=f"q_{index}",
        question_text=f"第 {index} 个问题？",
        target_fact_key=f"slot_{index}",
        purpose=QuestionPurpose.CHIEF_COMPLAINT_CHARACTERIZATION,
        target_layer=ConsultationLayer.L0_COLLECTION,
        risk_impact=RiskImpact.MEDIUM,
    )


def test_draft_rejects_more_than_three_selected_questions() -> None:
    """验证标准问诊草稿每轮最多允许三个追问。

    :return: None。
    """

    with pytest.raises(ValidationError):
        StandardConsultationDraftDto(
            task_id="task_1",
            current_pet_id="pet_1",
            status=DraftStatus.NEEDS_MORE_INFO,
            draft_response="需要继续追问。",
            draft_response_ref="draft:1",
            reached_layer=ConsultationLayer.L1_TRIAGE,
            selected_questions=[_build_question(index) for index in range(4)],
            trace_patch=_build_trace_patch(),
        )


def test_escalation_status_requires_structured_request() -> None:
    """验证安全升级草稿必须携带结构化升级请求。

    :return: None。
    """

    with pytest.raises(ValidationError):
        StandardConsultationDraftDto(
            task_id="task_1",
            current_pet_id="pet_1",
            status=DraftStatus.NEEDS_SAFETY_ESCALATION,
            draft_response="需要急症升级。",
            draft_response_ref="draft:1",
            reached_layer=ConsultationLayer.L1_TRIAGE,
            trace_patch=_build_trace_patch(),
        )


def test_escalation_status_accepts_structured_request() -> None:
    """验证安全升级草稿携带升级请求时可通过 DTO 校验。

    :return: None。
    """

    draft = StandardConsultationDraftDto(
        task_id="task_1",
        current_pet_id="pet_1",
        status=DraftStatus.NEEDS_SAFETY_ESCALATION,
        draft_response="需要急症升级。",
        draft_response_ref="draft:1",
        reached_layer=ConsultationLayer.L1_TRIAGE,
        escalation_request=EscalationRequestDto(
            reason_code="acute_flag",
            summary="出现急症红旗。",
        ),
        trace_patch=_build_trace_patch(),
    )

    assert draft.escalation_request is not None
    assert draft.escalation_request.target_profile == "safety_trigger"


def test_error_dto_uses_default_retryable_policy() -> None:
    """验证错误 DTO 会按错误码应用默认可重试策略。

    :return: None。
    """

    retryable = build_standard_consultation_error_dto(
        code=StandardConsultationErrorCode.STANDARD_RAG_DEGRADED,
        operation=StandardConsultationOperation.GENERATE_DRAFT,
        message="RAG 降级。",
    )
    blocked = build_standard_consultation_error_dto(
        code=StandardConsultationErrorCode.STANDARD_PROFILE_MISMATCH,
        operation=StandardConsultationOperation.GENERATE_DRAFT,
        message="剖面不匹配。",
    )

    assert retryable.retryable is True
    assert blocked.retryable is False
