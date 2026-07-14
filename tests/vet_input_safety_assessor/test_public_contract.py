##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_public_contract.py
# 作用: 验证 VetInputSafetyAssessor 组件公共导出、RuntimeConfig 接入和关闭态阻断行为。
# 边界: 只通过一级包出口导入生产对象，不接入真实 LLM、本地结构化抽取器或 LogicTraceStore。
##################################################################################################

import asyncio

import pytest

from tests.vet_input_safety_assessor.helpers import (
    build_batch_request,
    build_provider,
)
from veterinary_agent.config import VetInputSafetyAssessorSettings
import veterinary_agent.vet_input_safety_assessor as vet_input_safety_assessor
from veterinary_agent.vet_input_safety_assessor import (
    VetInputSafetyAssessorError,
    VetInputSafetyAssessorErrorCode,
    create_default_vet_input_safety_assessor,
)


def test_package_exposes_public_contract() -> None:
    """验证 VetInputSafetyAssessor 公共能力均可从一级包导入。

    :return: None。
    """

    expected_names: tuple[str, ...] = (
        "AssessmentMethod",
        "AssessmentStatus",
        "AssessmentTraceSummaryDto",
        "BatchVetInputAssessmentRequestDto",
        "BatchVetInputAssessmentResultDto",
        "DefaultVetInputSafetyAssessor",
        "DisambiguationMethod",
        "InputSafetySignalDto",
        "JsonMap",
        "KeywordLexicalSignalMatcher",
        "KeywordSemanticRouteClassifier",
        "KeywordSignalRule",
        "LexicalSignalMatcher",
        "LightweightAssessmentContextDto",
        "LlmArbitrationResultDto",
        "LogicTraceVetInputSafetyTraceSink",
        "ResolvedProfileDecisionDto",
        "RouteLabel",
        "SafetySignalCode",
        "SemanticRouteCandidateDto",
        "SemanticRouteClassifier",
        "SignalSource",
        "SignalStrength",
        "StructuredSignalExtractionSummaryDto",
        "StructuredSignalExtractor",
        "TODO_INPUT_SAFETY_TRACE_ERROR_CODE",
        "TODO_LOCAL_EXTRACTOR_VERSION",
        "TODO_SEMANTIC_ROUTER_VERSION",
        "TodoStructuredSignalExtractor",
        "TodoVetInputSafetyTraceSink",
        "VetInputAssessmentRequestDto",
        "VetInputAssessmentResultDto",
        "VetInputAssessmentTraceRecordDto",
        "VetInputAssessmentTraceWriteStatus",
        "VetInputSafetyAssessor",
        "VetInputSafetyAssessorDto",
        "VetInputSafetyAssessorError",
        "VetInputSafetyAssessorErrorCode",
        "VetInputSafetyAssessorErrorDto",
        "VetInputSafetyAssessorGraphNode",
        "VetInputSafetyAssessorOperation",
        "VetInputSafetyTraceSink",
        "VetInputSafetyTraceWriteResultDto",
        "VetIntent",
        "VetProfileDecisionResolver",
        "build_input_text_hash",
        "build_vet_input_safety_assessor_error_dto",
        "create_default_vet_input_safety_assessor",
        "is_vet_input_safety_assessor_error_retryable_by_default",
    )

    assert tuple(vet_input_safety_assessor.__all__) == expected_names
    for name in expected_names:
        assert hasattr(vet_input_safety_assessor, name)


def test_runtime_config_exposes_input_safety_settings() -> None:
    """验证 RuntimeConfig 快照暴露 VetInputSafetyAssessor 配置命名空间。

    :return: None。
    """

    provider = build_provider()
    snapshot = provider.current_snapshot()

    assert snapshot.vet_input_safety_assessor.enabled is True
    assert snapshot.vet_input_safety_assessor.config_version == (
        "vet-input-safety-assessor-config.v1"
    )
    assert create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
    ).is_ready()


def test_disabled_runtime_config_marks_service_not_ready() -> None:
    """验证配置关闭组件时服务 readiness 为 False 且调用被阻断。

    :return: None。
    """

    provider = build_provider(settings=VetInputSafetyAssessorSettings(enabled=False))
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
    )

    assert assessor.is_ready() is False
    with pytest.raises(VetInputSafetyAssessorError) as exc_info:
        asyncio.run(assessor.batch_assess(build_batch_request(provider)))

    assert exc_info.value.code is VetInputSafetyAssessorErrorCode.INPUT_ASSESS_NOT_READY
