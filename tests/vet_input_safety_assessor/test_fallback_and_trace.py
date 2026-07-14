##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_fallback_and_trace.py
# 作用: 验证 VetInputSafetyAssessor 弱依赖不可用、LLM 仲裁降级和 trace 写入旁路行为。
# 边界: 使用测试替身模拟弱依赖，不接入真实 LLM、UIE、本地模型或 LogicTraceStore。
##################################################################################################

import asyncio

from tests.vet_input_safety_assessor.helpers import (
    FakeAgentRunner,
    FakeSemanticRouteClassifier,
    FakeStructuredSignalExtractor,
    RecordingInputSafetyTraceSink,
    build_agent_result,
    build_batch_request,
    build_provider,
    build_task,
)
from veterinary_agent.config import VetInputSafetyAssessorSettings
from veterinary_agent.vet_context_builder import (
    VetExecutorKey,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor import (
    AssessmentMethod,
    AssessmentStatus,
    VetInputAssessmentTraceWriteStatus,
    create_default_vet_input_safety_assessor,
)
from veterinary_agent.vet_task_decomposer import VetTaskType


def test_semantic_router_unavailable_degrades_without_blocking() -> None:
    """验证语义路由不可用时评估结果降级但不阻断。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        semantic_classifier=FakeSemanticRouteClassifier(ready=False),
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[build_task(query="狗今天呕吐两次。", task_type=VetTaskType.TRIAGE)],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.status is AssessmentStatus.DEGRADED
    assert assessment.trace_summary.semantic_router_unavailable is True
    assert assessment.executor_key is VetExecutorKey.STANDARD_CONSULTATION


def test_local_extractor_unavailable_degrades_when_enabled() -> None:
    """验证本地结构化抽取器启用但不可用时评估降级。

    :return: None。
    """

    settings = VetInputSafetyAssessorSettings(local_extractor_enabled=True)
    provider = build_provider(settings=settings)
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        structured_extractor=FakeStructuredSignalExtractor(ready=False),
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[build_task(query="狗今天呕吐两次。", task_type=VetTaskType.TRIAGE)],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.status is AssessmentStatus.DEGRADED
    assert assessment.trace_summary.local_extractor_unavailable is True


def test_llm_arbitration_unavailable_falls_back_to_default() -> None:
    """验证低置信 LLM 仲裁不可用时进入非 LLM 降级路径。

    :return: None。
    """

    settings = VetInputSafetyAssessorSettings(llm_arbitration_enabled=True)
    provider = build_provider(settings=settings)
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        semantic_classifier=FakeSemanticRouteClassifier(candidates=[]),
        agent_runner=FakeAgentRunner(ready=False),
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="帮我看看这个问题。",
                task_type=VetTaskType.UNDECOMPOSED,
                confidence=0.2,
            )
        ],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.status is AssessmentStatus.DEGRADED
    assert assessment.trace_summary.llm_unavailable is True
    assert assessment.trace_summary.fallback_used is True
    assert assessment.trace_summary.method is AssessmentMethod.FALLBACK_DEFAULT


def test_llm_arbitration_success_can_resolve_low_confidence_input() -> None:
    """验证低置信 LLM 仲裁成功时可产出受控枚举裁决。

    :return: None。
    """

    settings = VetInputSafetyAssessorSettings(llm_arbitration_enabled=True)
    provider = build_provider(settings=settings)
    agent_runner = FakeAgentRunner(
        result=build_agent_result(
            parsed_output={
                "intent": "EDUCATION",
                "intent_confidence": 0.77,
                "route": "normal",
                "generation_profile": "education",
                "executor_key": "education",
                "compression_strategy": "education_light",
                "reason_code": "test_llm_education",
            }
        )
    )
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        semantic_classifier=FakeSemanticRouteClassifier(candidates=[]),
        agent_runner=agent_runner,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="帮我解释一下这个问题。",
                task_type=VetTaskType.UNDECOMPOSED,
                confidence=0.2,
            )
        ],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.generation_profile is VetGenerationProfile.EDUCATION
    assert assessment.executor_key is VetExecutorKey.EDUCATION
    assert assessment.trace_summary.method is AssessmentMethod.LLM_ARBITRATED
    assert len(agent_runner.requests) == 1


def test_trace_sink_exception_degrades_trace_without_changing_decision() -> None:
    """验证 trace sink 异常只降级 trace 状态，不改变核心裁决。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(exception=RuntimeError("trace down")),
    )
    request = build_batch_request(
        provider,
        tasks=[build_task(query="狗今天呕吐两次。", task_type=VetTaskType.TRIAGE)],
        original_user_message="狗今天呕吐两次。",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert result.trace_delivery_status is VetInputAssessmentTraceWriteStatus.DEGRADED
    assert (
        assessment.trace_delivery_status is VetInputAssessmentTraceWriteStatus.DEGRADED
    )
    assert assessment.executor_key is VetExecutorKey.STANDARD_CONSULTATION


def test_trace_record_is_redacted_and_uses_hash_reference() -> None:
    """验证 trace 摘要不保存用户原文而使用 hash 引用。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingInputSafetyTraceSink()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    original_message = "狗刚刚吃了布洛芬怎么办？"
    request = build_batch_request(
        provider,
        tasks=[build_task(query=original_message, task_type=VetTaskType.TRIAGE)],
        original_user_message=original_message,
    )

    result = asyncio.run(assessor.batch_assess(request))
    record = trace_sink.records[0]

    assert result.trace_delivery_status is VetInputAssessmentTraceWriteStatus.RECORDED
    assert record.original_user_message_hash is not None
    assert original_message not in str(record.model_dump(mode="json"))
