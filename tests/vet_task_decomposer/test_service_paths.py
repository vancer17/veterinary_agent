##################################################################################################
# 文件: tests/vet_task_decomposer/test_service_paths.py
# 作用: 验证 VetTaskDecomposer 的 LLM 主路径、有限审查修复、本地 fallback 和可观测性组件级行为。
# 边界: 使用测试替身模拟 AgentRunner 与 fallback，不调用真实模型、不执行 OCR、不进入后续安全评估。
##################################################################################################

import asyncio

from tests.vet_task_decomposer.helpers import (
    DEFAULT_USER_MESSAGE,
    FakeAgentRunner,
    FakeLocalFallback,
    FakeObservabilityProvider,
    build_agent_result,
    build_fallback_result,
    build_full_span_task,
    build_provider,
    build_request,
    method_metric_seen,
)
from veterinary_agent.vet_task_decomposer import (
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    VetTaskType,
    create_default_vet_task_decomposer,
)


def test_llm_success_result_is_normalized_and_contract_safe() -> None:
    """验证 LLM 成功输出会被归一化为安全子任务契约。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(
        results=[
            build_agent_result(
                parsed_output={
                    "tasks": [
                        {
                            "task_type": "TRIAGE",
                            "source_text": "狗狗今天呕吐两次",
                            "normalized_query": "狗狗今天呕吐两次",
                            "attachment_bindings": [
                                {
                                    "attachment_id": "missing_att",
                                    "attachment_role": "diagnostic_context",
                                }
                            ],
                            "confidence": 0.9,
                        },
                        {
                            "task_type": "REPORT_OCR",
                            "source_text": "看一下这个化验单",
                            "normalized_query": "看一下这个化验单",
                            "attachment_bindings": [
                                {
                                    "attachment_id": "att_1",
                                    "attachment_role": "independent_visual_task",
                                }
                            ],
                            "requires_independent_segment": True,
                            "confidence": 0.88,
                        },
                    ]
                }
            )
        ]
    )
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
    )

    result = asyncio.run(decomposer.decompose(build_request(provider)))

    assert result.status is DecompositionStatus.SUCCEEDED
    assert result.trace_summary.method is DecompositionMethod.LLM
    assert [task.task_type for task in result.tasks] == [
        VetTaskType.TRIAGE,
        VetTaskType.REPORT_OCR,
    ]
    assert all(task.current_pet_id == "pet_1" for task in result.tasks)
    assert result.tasks[0].attachment_bindings == []
    assert result.tasks[1].attachment_bindings[0].attachment_role is (
        AttachmentRole.INDEPENDENT_VISUAL_TASK
    )
    assert result.tasks[0].source_span.start_offset == 0
    assert result.tasks[0].source_span.end_offset == len("狗狗今天呕吐两次")
    assert len(agent_runner.requests) == 1


def test_low_confidence_llm_output_can_be_repaired_by_review_agent() -> None:
    """验证低置信主拆解结果会触发有限审查修复。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(
        results=[
            build_agent_result(
                parsed_output={
                    "tasks": [
                        {
                            "task_type": "TRIAGE",
                            "source_text": DEFAULT_USER_MESSAGE,
                            "confidence": 0.1,
                        }
                    ]
                }
            ),
            build_agent_result(
                parsed_output={
                    "tasks": [
                        {
                            "task_type": "TRIAGE",
                            "source_text": DEFAULT_USER_MESSAGE,
                            "normalized_query": DEFAULT_USER_MESSAGE,
                            "confidence": 0.82,
                        }
                    ]
                }
            ),
        ]
    )
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
    )

    result = asyncio.run(decomposer.decompose(build_request(provider)))

    assert result.trace_summary.method is DecompositionMethod.LLM_REVIEW_REPAIRED
    assert result.status is DecompositionStatus.SUCCEEDED
    assert len(agent_runner.requests) == 2
    assert agent_runner.requests[1].agent_id == "vet_task_decomposer_review"


def test_llm_unavailable_uses_valid_local_fallback_candidate() -> None:
    """验证 LLM 不可用时可采用满足契约的本地 fallback 候选。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    local_fallback = FakeLocalFallback(
        result=build_fallback_result(tasks=[build_full_span_task(request)])
    )
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        agent_runner=FakeAgentRunner(ready=False),
        local_fallback=local_fallback,
    )

    result = asyncio.run(decomposer.decompose(request))

    assert result.status is DecompositionStatus.DEGRADED
    assert result.trace_summary.method is DecompositionMethod.LOCAL_FALLBACK
    assert result.trace_summary.llm_unavailable is True
    assert result.tasks[0].task_type is VetTaskType.TRIAGE
    assert len(local_fallback.requests) == 1


def test_observability_records_decomposition_metrics_without_user_text() -> None:
    """验证组件记录指标且不把用户原文放入观测字段。

    :return: None。
    """

    provider = build_provider()
    observability = FakeObservabilityProvider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        observability_provider=observability,
    )

    result = asyncio.run(decomposer.decompose(build_request(provider)))

    assert result.trace_summary.method is DecompositionMethod.SINGLE_PASSTHROUGH
    assert method_metric_seen(
        observability.metrics,
        metric_name="vet_task_decomposer_task_count",
        method=DecompositionMethod.SINGLE_PASSTHROUGH,
    )
    assert DEFAULT_USER_MESSAGE not in repr(observability.metrics)
    assert DEFAULT_USER_MESSAGE not in repr(observability.events)
