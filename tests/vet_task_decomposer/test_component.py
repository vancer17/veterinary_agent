##################################################################################################
# 文件: tests/vet_task_decomposer/test_component.py
# 作用: 验证 VetTaskDecomposer 的公共契约、单任务透传降级和 GraphRuntime 节点适配行为。
# 边界: 只通过一级包出口导入组件能力，不调用内部实现模块、不接入真实 LLM 或本地预训练模型。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.config import (
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext, GraphState
from veterinary_agent.vet_task_decomposer import (
    AttachmentRefDto,
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    VetTaskDecomposeRequestDto,
    VetTaskDecomposerError,
    VetTaskDecomposerErrorCode,
    VetTaskDecomposerGraphNode,
    VetTaskType,
    create_default_vet_task_decomposer,
)


def _build_provider() -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :return: 已加载默认配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider()


def _build_request(
    provider: RuntimeConfigProvider,
    *,
    current_pet_id: str | None = "pet_1",
) -> VetTaskDecomposeRequestDto:
    """构建测试使用的任务拆解请求。

    :param provider: 已加载默认配置的 RuntimeConfig provider。
    :param current_pet_id: 可选当前宠物 ID；传入 None 用于阻断测试。
    :return: 可传给 VetTaskDecomposer 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    return VetTaskDecomposeRequestDto(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        session_id="sess_1",
        user_id="user_1",
        current_pet_id=current_pet_id,
        user_message="狗狗今天呕吐两次，还想让我看一下这个化验单。",
        attachments=[
            AttachmentRefDto(
                attachment_id="att_1",
                mime_type="image/png",
                declared_type="lab_report",
                upload_order=0,
            )
        ],
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def _build_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载默认配置的 RuntimeConfig provider。
    :return: 可传给图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="vet_task_decomposer",
        session_id="sess_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def test_single_passthrough_when_agent_runner_missing() -> None:
    """验证 AgentRunner 未接入时返回完整原文透传任务。

    :return: None。
    """

    provider = _build_provider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    )

    result = asyncio.run(decomposer.decompose(_build_request(provider)))

    assert result.status is DecompositionStatus.DEGRADED
    assert result.trace_summary.method is DecompositionMethod.SINGLE_PASSTHROUGH
    assert result.tasks[0].task_type is VetTaskType.UNDECOMPOSED
    assert result.tasks[0].current_pet_id == "pet_1"
    assert result.tasks[0].source_span.start_offset == 0
    assert result.tasks[0].source_span.end_offset == len(
        "狗狗今天呕吐两次，还想让我看一下这个化验单。"
    )
    assert result.tasks[0].attachment_bindings[0].attachment_role is (
        AttachmentRole.UNKNOWN
    )


def test_missing_current_pet_id_blocks_decomposition() -> None:
    """验证缺少 current_pet_id 时按稳定错误码阻断。

    :return: None。
    """

    provider = _build_provider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    )

    with pytest.raises(VetTaskDecomposerError) as exc_info:
        asyncio.run(
            decomposer.decompose(
                _build_request(provider, current_pet_id=None),
            )
        )

    assert exc_info.value.code is (
        VetTaskDecomposerErrorCode.TASK_DECOMPOSE_MISSING_CURRENT_PET_ID
    )


def test_graph_node_writes_task_decomposition_state() -> None:
    """验证 Graph 节点从 request 读取输入并写回拆解 state。

    :return: None。
    """

    provider = _build_provider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    )
    node = VetTaskDecomposerGraphNode(decomposer=decomposer)
    state: GraphState = {
        "request": {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "猫咪不吃饭，还附了一张化验单。",
                        }
                    ],
                }
            ],
            "attachments": [
                {
                    "attachment_id": "att_1",
                    "mime_type": "image/png",
                    "purpose": "lab_report",
                }
            ],
        }
    }

    result = asyncio.run(node(state, _build_context(provider)))

    assert result.state_patch["decomposition_status"] == "degraded"
    assert result.state_patch["task_count"] == 1
    assert result.state_patch["task_types"] == ["UNDECOMPOSED"]
    assert (
        result.state_patch["original_user_message"] == "猫咪不吃饭，还附了一张化验单。"
    )
