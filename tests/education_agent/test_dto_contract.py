##################################################################################################
# 文件: tests/education_agent/test_dto_contract.py
# 作用: 验证 EducationAgent DTO 的 DRAFT_READY 证据绑定关系和错误 DTO 默认重试策略。
# 边界: 只构造进程内 DTO，不调用 service、AgentRunner、RAG、Trace 存储或 GraphRuntime。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent.education_agent import (
    EducationContentPlanDto,
    EducationDraftDto,
    EducationDraftStatus,
    EducationAgentErrorCode,
    EducationAgentOperation,
    EducationTracePatchDto,
    EvidenceBindingDto,
    ExplanationDimensionCode,
    GroundingCheckSummaryDto,
    RagUsageSummaryDto,
    build_education_agent_error_dto,
)


def _build_content_plan() -> EducationContentPlanDto:
    """构建测试使用的科普内容计划。

    :return: 可放入草稿 DTO 的内容计划。
    """

    return EducationContentPlanDto(
        main_axis="抽搐科普",
        section_titles=["定义"],
        selected_dimensions=[ExplanationDimensionCode.DEFINITION],
    )


def _build_trace_patch() -> EducationTracePatchDto:
    """构建测试使用的科普 trace patch。

    :return: 可放入草稿 DTO 的 trace patch。
    """

    return EducationTracePatchDto(
        education_agent_version="education-agent.v1",
        planner_version="education-planner.v1",
        writer_version="education-writer.v1",
        selected_dimensions=[ExplanationDimensionCode.DEFINITION],
        retrieval_ids=["retrieval_1"],
    )


def test_draft_ready_requires_evidence_bindings() -> None:
    """验证 DRAFT_READY 科普草稿必须包含证据绑定。

    :return: None。
    """

    with pytest.raises(ValidationError):
        EducationDraftDto(
            task_id="task_1",
            current_pet_id="pet_1",
            status=EducationDraftStatus.DRAFT_READY,
            draft_response="科普草稿。",
            draft_response_ref="draft:1",
            content_plan=_build_content_plan(),
            rag_summary=RagUsageSummaryDto(
                rag_invoked=True,
                retrieval_ids=["retrieval_1"],
            ),
            grounding_check=GroundingCheckSummaryDto(),
            trace_patch=_build_trace_patch(),
        )


def test_draft_ready_requires_rag_invoked() -> None:
    """验证 DRAFT_READY 科普草稿必须记录 RAG 已调用。

    :return: None。
    """

    with pytest.raises(ValidationError):
        EducationDraftDto(
            task_id="task_1",
            current_pet_id="pet_1",
            status=EducationDraftStatus.DRAFT_READY,
            draft_response="科普草稿。",
            draft_response_ref="draft:1",
            content_plan=_build_content_plan(),
            evidence_bindings=[
                EvidenceBindingDto(
                    claim_id="claim_1",
                    evidence_card_ids=["ecard_1"],
                    retrieval_ids=["retrieval_1"],
                    binding_summary="证据支持。",
                )
            ],
            rag_summary=RagUsageSummaryDto(rag_invoked=False),
            grounding_check=GroundingCheckSummaryDto(),
            trace_patch=_build_trace_patch(),
        )


def test_error_dto_uses_default_retryable_policy() -> None:
    """验证错误 DTO 会按错误码应用默认可重试策略。

    :return: None。
    """

    retryable = build_education_agent_error_dto(
        code=EducationAgentErrorCode.EDUCATION_RAG_DEGRADED,
        operation=EducationAgentOperation.GENERATE_DRAFT,
        message="RAG 降级。",
    )
    blocked = build_education_agent_error_dto(
        code=EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH,
        operation=EducationAgentOperation.GENERATE_DRAFT,
        message="剖面不匹配。",
    )

    assert retryable.retryable is True
    assert blocked.retryable is False
