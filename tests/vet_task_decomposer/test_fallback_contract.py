##################################################################################################
# 文件: tests/vet_task_decomposer/test_fallback_contract.py
# 作用: 验证 VetTaskDecomposer 对本地 fallback 候选执行当前宠物与 source span 契约过滤。
# 边界: 使用测试替身模拟本地 fallback，不加载真实预训练模型、不调用 LLM、不进入后续安全评估。
##################################################################################################

import asyncio

from tests.vet_task_decomposer.helpers import (
    FakeAgentRunner,
    FakeLocalFallback,
    build_fallback_result,
    build_full_span_task,
    build_provider,
    build_request,
)
from veterinary_agent.vet_task_decomposer import (
    DecompositionMethod,
    VetTaskType,
    create_default_vet_task_decomposer,
)


def test_invalid_local_fallback_candidates_fall_back_to_passthrough() -> None:
    """验证 fallback 候选宠物归属或 hash 非法时退回单任务透传。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    local_fallback = FakeLocalFallback(
        result=build_fallback_result(
            tasks=[
                build_full_span_task(request, current_pet_id="other_pet"),
                build_full_span_task(request, valid_hash=False),
            ]
        )
    )
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        agent_runner=FakeAgentRunner(ready=False),
        local_fallback=local_fallback,
    )

    result = asyncio.run(decomposer.decompose(request))

    assert result.trace_summary.method is DecompositionMethod.SINGLE_PASSTHROUGH
    assert result.tasks[0].task_type is VetTaskType.UNDECOMPOSED


def test_local_fallback_low_confidence_falls_back_to_passthrough() -> None:
    """验证 fallback 整体置信度低于阈值时退回单任务透传。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    local_fallback = FakeLocalFallback(
        result=build_fallback_result(
            tasks=[build_full_span_task(request)],
            confidence=0.1,
        )
    )
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        agent_runner=FakeAgentRunner(ready=False),
        local_fallback=local_fallback,
    )

    result = asyncio.run(decomposer.decompose(request))

    assert result.trace_summary.method is DecompositionMethod.SINGLE_PASSTHROUGH
    assert result.tasks[0].task_type is VetTaskType.UNDECOMPOSED
