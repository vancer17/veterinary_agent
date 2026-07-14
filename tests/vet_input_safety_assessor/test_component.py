##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_component.py
# 作用: 验证 VetInputSafetyAssessor 的 SAF 优先级、剖面裁决、非医疗输出和上下文构建请求兼容性。
# 边界: 不接入真实 LLM、UIE、本地模型或 LogicTraceStore；弱依赖使用默认本地兜底与测试 trace sink。
##################################################################################################

import asyncio
from typing import cast

from pydantic import JsonValue

from tests.vet_input_safety_assessor.helpers import (
    FakeSemanticRouteClassifier,
    RecordingInputSafetyTraceSink,
    build_batch_request,
    build_provider,
    build_semantic_candidate,
    build_task,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetContextBuildRequestDto,
    VetExecutorKey,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor import (
    RouteLabel,
    SafetySignalCode,
    VetInputAssessmentTraceWriteStatus,
    create_default_vet_input_safety_assessor,
)
from veterinary_agent.vet_task_decomposer import VetTaskType


def test_saf01_uses_deterministic_safety_trigger() -> None:
    """验证 SAF-01 毒物信号进入确定性 safety_trigger 裁决。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingInputSafetyTraceSink()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="我家狗刚刚吃了布洛芬怎么办？",
                task_type=VetTaskType.TRIAGE,
            )
        ],
        original_user_message="我家狗刚刚吃了布洛芬怎么办？",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.route is RouteLabel.SAFETY_TRIGGER
    assert assessment.generation_profile is VetGenerationProfile.SAFETY_TRIGGER
    assert assessment.executor_key is VetExecutorKey.SAFETY_TRIGGER
    assert assessment.compression_strategy is ContextCompressionStrategy.SAFETY_MINIMAL
    assert SafetySignalCode.SAF_01_TOXIC_SUBSTANCE in {
        signal.code for signal in assessment.signals
    }
    assert result.trace_delivery_status is VetInputAssessmentTraceWriteStatus.RECORDED
    assert len(trace_sink.records) == 1


def test_saf01_is_not_overridden_by_education_semantic_candidate() -> None:
    """验证 SAF-01 不会被语义路由的 education 候选降级。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        semantic_classifier=FakeSemanticRouteClassifier(
            candidates=[build_semantic_candidate(label="education")]
        ),
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="猫能不能吃一点对乙酰氨基酚？",
                task_type=VetTaskType.EDUCATION_QA,
            )
        ],
        original_user_message="猫能不能吃一点对乙酰氨基酚？",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.route is RouteLabel.SAFETY_TRIGGER
    assert assessment.generation_profile is VetGenerationProfile.SAFETY_TRIGGER
    assert assessment.executor_key is VetExecutorKey.SAFETY_TRIGGER


def test_saf03_education_question_keeps_signal_but_uses_education_profile() -> None:
    """验证 SAF-03 科普问法保留信号但不自动触发 safety_trigger。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="狗抽搐有哪些原因？",
                task_type=VetTaskType.EDUCATION_QA,
            )
        ],
        original_user_message="狗抽搐有哪些原因？",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.route is RouteLabel.NORMAL
    assert assessment.generation_profile is VetGenerationProfile.EDUCATION
    assert assessment.executor_key is VetExecutorKey.EDUCATION
    assert SafetySignalCode.SAF_03_ACUTE_RED_FLAG in {
        signal.code for signal in assessment.signals
    }


def test_standard_triage_uses_standard_consultation_profile() -> None:
    """验证普通症状咨询进入 standard_consultation。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="狗今天呕吐两次，精神一般。",
                task_type=VetTaskType.TRIAGE,
            )
        ],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.route is RouteLabel.NORMAL
    assert assessment.generation_profile is VetGenerationProfile.STANDARD
    assert assessment.executor_key is VetExecutorKey.STANDARD_CONSULTATION
    assert assessment.compression_strategy is ContextCompressionStrategy.SINGLE_FULL


def test_report_task_uses_lab_report_interpretation_executor() -> None:
    """验证报告类任务使用 lab_report_interpretation 执行器和 standard 剖面。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="帮我解读这张化验单。",
                task_type=VetTaskType.REPORT_OCR,
            )
        ],
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.generation_profile is VetGenerationProfile.STANDARD
    assert assessment.executor_key is VetExecutorKey.LAB_REPORT_INTERPRETATION
    assert assessment.compression_strategy is ContextCompressionStrategy.SINGLE_FULL


def test_saf03_realtime_uses_safety_trigger() -> None:
    """验证 SAF-03 实况红线进入 safety_trigger。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="我家猫现在尿不出来，一直蹲猫砂盆。",
                task_type=VetTaskType.TRIAGE,
            )
        ],
        original_user_message="我家猫现在尿不出来，一直蹲猫砂盆。",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.route is RouteLabel.SAFETY_TRIGGER
    assert assessment.executor_key is VetExecutorKey.SAFETY_TRIGGER
    assert assessment.generation_profile is VetGenerationProfile.SAFETY_TRIGGER


def test_nonmedical_task_keeps_generation_profile_empty() -> None:
    """验证纯非医疗任务使用 nonmedical_pet_care 执行器且 generation_profile 为空。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    request = build_batch_request(
        provider,
        tasks=[
            build_task(
                query="狗狗换粮后有点软便，怎么过渡更合适？",
                task_type=VetTaskType.NUTRITION,
            )
        ],
        original_user_message="狗狗换粮后有点软便，怎么过渡更合适？",
    )

    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    assert assessment.generation_profile is None
    assert assessment.executor_key is VetExecutorKey.NONMEDICAL_PET_CARE
    assert assessment.compression_strategy is ContextCompressionStrategy.EDUCATION_LIGHT
    assert SafetySignalCode.CROSS_DOMAIN_SYMPTOM in {
        signal.code for signal in assessment.signals
    }


def test_multi_task_assessments_are_independent_by_task_id() -> None:
    """验证多任务场景下 SAF 信号不会覆盖其他子任务的独立裁决。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    toxic_task = build_task(
        query="狗刚刚吃了布洛芬怎么办？",
        task_type=VetTaskType.TRIAGE,
        task_id="task_toxic",
    )
    nutrition_task = build_task(
        query="狗狗平时换粮怎么过渡？",
        task_type=VetTaskType.NUTRITION,
        task_id="task_nutrition",
    )
    request = build_batch_request(
        provider,
        tasks=[toxic_task, nutrition_task],
        original_user_message="狗刚刚吃了布洛芬，另外平时换粮怎么过渡？",
    )

    result = asyncio.run(assessor.batch_assess(request))
    results_by_task_id = {item.task_id: item for item in result.results}

    assert (
        results_by_task_id["task_toxic"].executor_key is VetExecutorKey.SAFETY_TRIGGER
    )
    assert (
        results_by_task_id["task_nutrition"].executor_key
        is VetExecutorKey.NONMEDICAL_PET_CARE
    )
    assert results_by_task_id["task_nutrition"].generation_profile is None


def test_assessment_result_builds_context_request_contract() -> None:
    """验证输入安全输出可直接映射为 VetContextBuilder 请求契约。

    :return: None。
    """

    provider = build_provider()
    snapshot = provider.current_snapshot()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    task = build_task(
        query="狗今天呕吐两次，精神一般。",
        task_type=VetTaskType.TRIAGE,
    )
    request = build_batch_request(provider, tasks=[task])
    result = asyncio.run(assessor.batch_assess(request))
    assessment = result.results[0]

    context_request = VetContextBuildRequestDto(
        request_id=request.request_id,
        trace_id=request.trace_id,
        run_id=request.run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        current_pet_id=request.current_pet_id,
        task_id=task.task_id,
        task_type=task.task_type.value,
        normalized_query=task.normalized_query,
        generation_profile=assessment.generation_profile,
        route=assessment.route.value,
        executor_key=assessment.executor_key,
        compression_strategy=assessment.compression_strategy,
        audit_tier=assessment.audit_tier_floor,
        assessment_summary=cast(dict[str, JsonValue], assessment.assessment_summary),
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )

    assert context_request.task_id == task.task_id
    assert context_request.generation_profile is VetGenerationProfile.STANDARD
