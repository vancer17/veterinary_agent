##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_dto_contract.py
# 作用: 验证 VetInputSafetyAssessor DTO 的宠物边界、结果唯一性和剖面执行器组合契约。
# 边界: 只构造严格 DTO，不调用真实服务、不访问外部依赖、不执行业务图。
##################################################################################################

import pytest
from pydantic import ValidationError

from tests.vet_input_safety_assessor.helpers import (
    build_batch_request,
    build_provider,
    build_task,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetAuditTier,
    VetExecutorKey,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor import (
    AssessmentMethod,
    AssessmentStatus,
    AssessmentTraceSummaryDto,
    BatchVetInputAssessmentResultDto,
    DisambiguationMethod,
    RouteLabel,
    VetInputAssessmentResultDto,
    VetIntent,
)
from veterinary_agent.vet_task_decomposer import VetTaskType


def _build_trace_summary() -> AssessmentTraceSummaryDto:
    """构建测试使用的输入安全 trace 摘要。

    :return: 可用于组装评估结果的 trace 摘要 DTO。
    """

    return AssessmentTraceSummaryDto(
        assessor_version="test",
        method=AssessmentMethod.SEMANTIC_ROUTER,
        llm_unavailable=False,
        semantic_router_unavailable=False,
        local_extractor_unavailable=False,
        fallback_used=False,
        signal_codes=[],
        final_decision_reason_code="test",
    )


def _build_assessment_result(
    *,
    task_id: str = "task_1",
    generation_profile: VetGenerationProfile | None = VetGenerationProfile.STANDARD,
    route: RouteLabel = RouteLabel.NORMAL,
    executor_key: VetExecutorKey = VetExecutorKey.STANDARD_CONSULTATION,
    compression_strategy: ContextCompressionStrategy = (
        ContextCompressionStrategy.SINGLE_FULL
    ),
) -> VetInputAssessmentResultDto:
    """构建测试使用的输入安全评估结果。

    :param task_id: 子任务 ID。
    :param generation_profile: 生成剖面。
    :param route: 输入安全路由。
    :param executor_key: 实际执行器。
    :param compression_strategy: 上下文压缩策略。
    :return: 输入安全评估结果 DTO。
    """

    return VetInputAssessmentResultDto(
        task_id=task_id,
        current_pet_id="pet_1",
        status=AssessmentStatus.SUCCEEDED,
        signals=[],
        intent=VetIntent.SYMPTOM_TRIAGE,
        intent_confidence=0.8,
        generation_profile=generation_profile,
        route=route,
        executor_key=executor_key,
        compression_strategy=compression_strategy,
        disambiguation_method=DisambiguationMethod.SEMANTIC_ROUTER,
        audit_tier_floor=VetAuditTier.A,
        assessment_summary={},
        trace_summary=_build_trace_summary(),
    )


def test_batch_request_rejects_mixed_current_pet_ids() -> None:
    """验证批量请求拒绝混用不同 current_pet_id 的子任务。

    :return: None。
    """

    provider = build_provider()

    with pytest.raises(ValidationError):
        build_batch_request(
            provider,
            tasks=[
                build_task(query="狗今天呕吐。", current_pet_id="pet_1"),
                build_task(
                    query="狗需要换粮。",
                    task_type=VetTaskType.NUTRITION,
                    current_pet_id="pet_2",
                    task_id="task_pet_2",
                ),
            ],
        )


def test_batch_result_rejects_duplicate_task_ids() -> None:
    """验证批量结果拒绝重复 task_id。

    :return: None。
    """

    with pytest.raises(ValidationError):
        BatchVetInputAssessmentResultDto(
            results=[
                _build_assessment_result(task_id="task_dup"),
                _build_assessment_result(task_id="task_dup"),
            ],
            status=AssessmentStatus.SUCCEEDED,
        )


def test_standard_executor_requires_standard_profile() -> None:
    """验证 standard 执行器必须使用 standard 剖面。

    :return: None。
    """

    with pytest.raises(ValidationError):
        _build_assessment_result(
            generation_profile=VetGenerationProfile.EDUCATION,
            executor_key=VetExecutorKey.STANDARD_CONSULTATION,
            compression_strategy=ContextCompressionStrategy.SINGLE_FULL,
        )


def test_safety_trigger_requires_safety_route_and_minimal_context() -> None:
    """验证 safety_trigger 执行器必须使用安全路由和 safety_minimal。

    :return: None。
    """

    with pytest.raises(ValidationError):
        _build_assessment_result(
            generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
            route=RouteLabel.NORMAL,
            executor_key=VetExecutorKey.SAFETY_TRIGGER,
            compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
        )


def test_nonmedical_executor_requires_empty_generation_profile() -> None:
    """验证纯非医疗执行器的 generation_profile 必须为空。

    :return: None。
    """

    with pytest.raises(ValidationError):
        _build_assessment_result(
            generation_profile=VetGenerationProfile.EDUCATION,
            executor_key=VetExecutorKey.NONMEDICAL_PET_CARE,
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
        )


def test_nonmedical_executor_accepts_empty_generation_profile() -> None:
    """验证纯非医疗执行器接受空 generation_profile。

    :return: None。
    """

    result = _build_assessment_result(
        generation_profile=None,
        executor_key=VetExecutorKey.NONMEDICAL_PET_CARE,
        compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
    )

    assert result.generation_profile is None
    assert result.executor_key is VetExecutorKey.NONMEDICAL_PET_CARE
