##################################################################################################
# 文件: tests/graph_runtime/test_default_runtime.py
# 作用: 验证基于 LangGraph 的 GraphRuntime 默认实现、事件流、checkpoint 恢复与控制面边界。
# 边界: 使用测试 CheckpointStore 与 InMemorySaver，不连接数据库、不实现真实 L2 兽医业务组件。
##################################################################################################

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from .helpers import (
    CapturingCheckpointStore,
    build_graph_request,
    build_todo_runtime,
    collect_events,
)
from veterinary_agent.agent_application_service import (
    AgentGraphRuntimeUnavailableError,
    AgentResumeTurnCommandDto,
)
from veterinary_agent.graph_runtime import (
    DefaultGraphRuntime,
    GraphDefinition,
    GraphEdgeSpec,
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphNodeSpec,
    GraphRegistry,
    GraphRuntimeError,
    GraphRuntimeEventType,
    GraphRuntimeSettings,
    GraphState,
)


class PatchNode:
    """测试用图节点。"""

    def __init__(
        self,
        *,
        key: str,
        value: object,
        selected_next_nodes: tuple[str, ...] | None = None,
    ) -> None:
        """初始化测试节点。

        :param key: 节点写入状态的字段名。
        :param value: 节点写入状态的字段值。
        :param selected_next_nodes: 可选显式选择的后继节点列表。
        :return: None。
        """

        self.key = key
        self.value = value
        self.selected_next_nodes = selected_next_nodes

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """执行测试节点并返回状态补丁。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 测试节点结果。
        """

        del state, context
        return GraphNodeResult(
            state_patch={self.key: self.value},
            selected_next_nodes=self.selected_next_nodes,
        )


class FailOnceNode:
    """第一次调用失败、恢复后成功的测试节点。"""

    def __init__(self) -> None:
        """初始化失败一次的测试节点。

        :return: None。
        """

        self.calls = 0

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """第一次抛出异常，第二次返回最终结果。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 成功恢复后的最终结果补丁。
        :raises RuntimeError: 第一次调用时用于模拟节点失败。
        """

        del state, context
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("planned failure")
        return GraphNodeResult(
            state_patch={
                "result": {
                    "output_text": "resume done",
                    "segments": [],
                    "metadata": {"resumed": True},
                }
            }
        )


def test_default_graph_runtime_executes_todo_vet_graph_with_langgraph_checkpoint() -> (
    None
):
    """验证默认 GraphRuntime 可执行 TODO 业务图并由 LangGraph 写入 checkpoint。

    :return: None。
    """

    checkpoint_store = CapturingCheckpointStore()
    runtime = build_todo_runtime(checkpoint_store)

    result = asyncio.run(runtime.execute_turn(build_graph_request()))

    assert runtime.is_ready() is True
    assert result.segments
    assert result.metadata["graph_runtime_degraded"] is True
    assert len(checkpoint_store.ensure_calls) == 1
    assert len(checkpoint_store.acquire_calls) == 1
    assert len(checkpoint_store.release_calls) == 1
    assert checkpoint_store.save_calls == []
    assert len(checkpoint_store.publish_calls) == 1


def test_default_graph_runtime_streams_standard_events() -> None:
    """验证默认 GraphRuntime 流式执行会产出标准事件。

    :return: None。
    """

    checkpoint_store = CapturingCheckpointStore()
    runtime = build_todo_runtime(checkpoint_store)

    events = asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))
    event_types = [event.event_type for event in events]

    assert GraphRuntimeEventType.RUN_STARTED.value in event_types
    assert GraphRuntimeEventType.CHECKPOINT_SAVED.value in event_types
    assert GraphRuntimeEventType.SEGMENT_READY.value in event_types
    assert GraphRuntimeEventType.SEGMENT_PUBLISHED.value in event_types
    assert GraphRuntimeEventType.RUN_COMPLETED.value in event_types


def test_default_graph_runtime_fails_closed_without_checkpoint_store() -> None:
    """验证缺少 CheckpointStore 时 GraphRuntime 不就绪并拒绝执行。

    :return: None。
    """

    runtime = DefaultGraphRuntime(checkpointer=InMemorySaver())

    assert runtime.is_ready() is False
    with pytest.raises(AgentGraphRuntimeUnavailableError):
        asyncio.run(runtime.execute_turn(build_graph_request()))


def test_default_graph_runtime_supports_selected_parallel_branches() -> None:
    """验证 GraphRuntime 支持条件选择后的并行分支执行。

    :return: None。
    """

    registry = GraphRegistry()
    registry.register(
        GraphDefinition(
            graph_id="branch_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="start",
            nodes={
                "start": GraphNodeSpec(
                    node_id="start",
                    handler=PatchNode(
                        key="start_done",
                        value=True,
                        selected_next_nodes=("left", "right"),
                    ),
                ),
                "left": GraphNodeSpec(
                    node_id="left",
                    handler=PatchNode(key="left_done", value=True),
                ),
                "right": GraphNodeSpec(
                    node_id="right",
                    handler=PatchNode(
                        key="result",
                        value={
                            "output_text": "branch done",
                            "segments": [],
                            "metadata": {"branch_graph": True},
                        },
                    ),
                ),
                "skipped": GraphNodeSpec(
                    node_id="skipped",
                    handler=PatchNode(key="skipped_done", value=True),
                ),
            },
            edges=(
                GraphEdgeSpec(from_node="start", to_node="left", kind="conditional"),
                GraphEdgeSpec(from_node="start", to_node="right", kind="conditional"),
                GraphEdgeSpec(from_node="start", to_node="skipped", kind="conditional"),
            ),
        )
    )
    checkpoint_store = CapturingCheckpointStore()
    runtime = DefaultGraphRuntime(
        checkpoint_store=checkpoint_store,
        checkpointer=InMemorySaver(),
        graph_registry=registry,
        settings=GraphRuntimeSettings(
            graph_id="branch_graph",
            graph_version="v1",
        ),
    )

    events = asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))
    completed_node_ids = [
        event.data.get("node_id")
        for event in events
        if event.event_type == GraphRuntimeEventType.NODE_COMPLETED.value
    ]

    assert completed_node_ids == ["start", "left", "right"]
    assert "skipped" not in completed_node_ids


def test_default_graph_runtime_resumes_failed_langgraph_checkpoint() -> None:
    """验证 GraphRuntime 可按 LangGraph checkpoint 中的图版本恢复失败运行。

    :return: None。
    """

    fail_once_node = FailOnceNode()
    registry = GraphRegistry()
    registry.register(
        GraphDefinition(
            graph_id="resume_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="unstable",
            nodes={
                "unstable": GraphNodeSpec(
                    node_id="unstable",
                    handler=fail_once_node,
                    max_attempts=1,
                )
            },
        )
    )
    checkpointer = InMemorySaver()
    checkpoint_store = CapturingCheckpointStore()
    runtime = DefaultGraphRuntime(
        checkpoint_store=checkpoint_store,
        checkpointer=checkpointer,
        graph_registry=registry,
        settings=GraphRuntimeSettings(
            graph_id="resume_graph",
            graph_version="v1",
        ),
    )

    with pytest.raises(GraphRuntimeError):
        asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))
    resume_events = asyncio.run(
        collect_events(
            runtime.resume_turn(
                AgentResumeTurnCommandDto(
                    request_id="req_resume",
                    trace_id="trace_1",
                    run_id="run_resume",
                    checkpoint_ref="checkpoint_thread_test",
                )
            )
        )
    )
    completed_event = next(
        event
        for event in resume_events
        if event.event_type == GraphRuntimeEventType.RUN_COMPLETED.value
    )

    assert completed_event.data["graph_id"] == "resume_graph"
    assert completed_event.data["graph_version"] == "v1"
    assert completed_event.data["output_text"] == "resume done"
    assert fail_once_node.calls == 2
