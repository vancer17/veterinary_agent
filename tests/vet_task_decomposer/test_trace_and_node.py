##################################################################################################
# 文件: tests/vet_task_decomposer/test_trace_and_node.py
# 作用: 验证 VetTaskDecomposer trace 写入降级语义和 GraphRuntime 节点 state 适配契约。
# 边界: 使用测试 trace sink，不写真实 LogicTraceStore、不切换真实业务图、不调用后续安全评估组件。
##################################################################################################

import asyncio

from tests.vet_task_decomposer.helpers import (
    DEFAULT_USER_MESSAGE,
    FakeTraceSink,
    build_provider,
    build_request,
)
from veterinary_agent.config import RuntimeConfigProvider
from veterinary_agent.graph_runtime import GraphNodeExecutionContext, GraphState
from veterinary_agent.vet_task_decomposer import (
    VetTaskDecomposerGraphNode,
    VetTaskTraceWriteStatus,
    build_text_hash,
    create_default_vet_task_decomposer,
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


def test_trace_summary_is_recorded_without_raw_user_message() -> None:
    """验证 trace sink 接收脱敏摘要且不包含用户原文。

    :return: None。
    """

    provider = build_provider()
    trace_sink = FakeTraceSink()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(decomposer.decompose(build_request(provider)))

    assert result.trace_delivery_status is VetTaskTraceWriteStatus.RECORDED
    assert len(trace_sink.records) == 1
    record = trace_sink.records[0]
    assert record.input_text_hash == build_text_hash(DEFAULT_USER_MESSAGE)
    assert DEFAULT_USER_MESSAGE not in repr(record)


def test_trace_sink_failure_degrades_without_blocking_result() -> None:
    """验证 trace sink 异常不会阻断任务拆解主结果。

    :return: None。
    """

    provider = build_provider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
        trace_sink=FakeTraceSink(raise_on_write=True),
    )

    result = asyncio.run(decomposer.decompose(build_request(provider)))

    assert result.trace_delivery_status is VetTaskTraceWriteStatus.DEGRADED
    assert result.tasks


def test_graph_node_extracts_text_and_attachments_into_state_patch() -> None:
    """验证 Graph 节点提取文本、附件并写回标准 state patch。

    :return: None。
    """

    provider = build_provider()
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
                        {"type": "input_text", "text": "猫咪不吃饭。"},
                        {"type": "input_attachment", "attachment_id": "att_1"},
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "还附了一张化验单。"},
                    ],
                },
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

    assert (
        result.state_patch["original_user_message"]
        == "猫咪不吃饭。\n还附了一张化验单。"
    )
    assert result.state_patch["task_count"] == 1
    assert result.state_patch["task_types"] == ["UNDECOMPOSED"]
