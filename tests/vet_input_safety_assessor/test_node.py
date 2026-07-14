##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_node.py
# 作用: 验证 VetInputSafetyAssessorGraphNode 的 graph state 读取、状态写回和上下文构建请求映射。
# 边界: 只测试节点适配层，不调度后继节点、不接入真实 LangGraph checkpointer 或外部服务。
##################################################################################################

import asyncio
from typing import cast

from pydantic import JsonValue
import pytest

from tests.vet_input_safety_assessor.helpers import (
    RecordingInputSafetyTraceSink,
    build_graph_context,
    build_provider,
    build_task,
)
from veterinary_agent.vet_context_builder import (
    VetContextBuildRequestDto,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor import (
    VetInputSafetyAssessorError,
    VetInputSafetyAssessorErrorCode,
    VetInputSafetyAssessorGraphNode,
    create_default_vet_input_safety_assessor,
)
from veterinary_agent.vet_task_decomposer import VetTaskType


def test_graph_node_writes_assessment_and_context_requests() -> None:
    """验证 GraphNode 写回评估结果、摘要映射和上下文构建请求。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    node = VetInputSafetyAssessorGraphNode(assessor=assessor)
    context = build_graph_context(provider)
    task = build_task(query="狗今天呕吐两次。", task_type=VetTaskType.TRIAGE)
    state: dict[str, object] = {
        "vet_sub_tasks": [task.model_dump(mode="json")],
        "original_user_message": "狗今天呕吐两次。",
    }

    node_result = asyncio.run(node(state, context))
    patch = node_result.state_patch

    assert "vet_input_assessment_results" in patch
    assert "assessment_summary_by_task_id" in patch
    assert "context_build_request" in patch
    raw_context_request = cast(dict[str, object], patch["context_build_request"])
    context_request_data = {
        **raw_context_request,
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "user_id": context.user_id,
        "current_pet_id": context.current_pet_id,
        "params_version": context.params_version,
        "config_snapshot_id": context.config_snapshot_id,
        "assessment_summary": cast(
            dict[str, JsonValue],
            raw_context_request["assessment_summary"],
        ),
    }
    context_request = VetContextBuildRequestDto(**context_request_data)

    assert context_request.task_id == task.task_id
    assert context_request.generation_profile is VetGenerationProfile.STANDARD


def test_graph_node_rejects_missing_sub_tasks() -> None:
    """验证 GraphNode 在缺少 vet_sub_tasks 时抛出稳定错误码。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        trace_sink=RecordingInputSafetyTraceSink(),
    )
    node = VetInputSafetyAssessorGraphNode(assessor=assessor)
    context = build_graph_context(provider)

    with pytest.raises(VetInputSafetyAssessorError) as exc_info:
        asyncio.run(node({}, context))

    assert (
        exc_info.value.code
        is VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST
    )
