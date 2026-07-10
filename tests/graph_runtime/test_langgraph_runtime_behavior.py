##################################################################################################
# 文件: tests/graph_runtime/test_langgraph_runtime_behavior.py
# 作用: 验证 GraphRuntime 复用 LangGraph 执行内核后的路由校验、segment 幂等、锁释放与事件开关行为。
# 边界: 通过 GraphRuntime 公共入口触发 LangGraph，不直接调用后端内部实现，不连接真实数据库。
##################################################################################################

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from .helpers import (
    CapturingCheckpointStore,
    build_graph_request,
    collect_events,
)
from veterinary_agent import (
    AgentResponseSegmentDto,
    DefaultGraphRuntime,
    GraphDefinition,
    GraphEdgeSpec,
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphNodeSpec,
    GraphRegistry,
    GraphRuntimeError,
    GraphRuntimeErrorCode,
    GraphRuntimeEventType,
    GraphRuntimeSettings,
    GraphState,
)


class SegmentNode:
    """产出待发布 segment 与最终结果的测试节点。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """返回携带 task_id 的 segment 与最终结果。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 包含 segment 发布状态的节点结果。
        """

        del state
        segment = AgentResponseSegmentDto(
            segment_id=f"segment_{context.run_id}_one",
            type="answer",
            title="测试片段",
            status="completed",
            output_text="segment done",
            metadata={"task_id": "task_segment"},
        )
        result = {
            "output_text": "segment done",
            "segments": [segment.model_dump(mode="json")],
            "metadata": {"segment_node": True},
        }
        return GraphNodeResult(
            state_patch={
                "result": result,
                "segments": [segment.model_dump(mode="json")],
                "segments_to_publish": [segment.model_dump(mode="json")],
            }
        )


class InvalidRouteNode:
    """选择未声明条件后继节点的测试节点。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """返回非法后继节点选择。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 携带非法路由选择的节点结果。
        """

        del state, context
        return GraphNodeResult(
            state_patch={"route_attempted": True},
            selected_next_nodes=("missing_node",),
        )


class FailingNode:
    """始终失败的测试节点。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """抛出计划内异常。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 本方法不会正常返回。
        :raises RuntimeError: 始终用于模拟节点失败。
        """

        del state, context
        raise RuntimeError("planned failure")


def build_runtime_for_definition(
    definition: GraphDefinition,
    *,
    settings: GraphRuntimeSettings | None = None,
    checkpoint_store: CapturingCheckpointStore | None = None,
) -> tuple[DefaultGraphRuntime, CapturingCheckpointStore]:
    """按指定图定义构建测试 GraphRuntime。

    :param definition: 需要注册并编译的图定义。
    :param settings: 可选 GraphRuntime 设置。
    :param checkpoint_store: 可选测试控制面存储。
    :return: GraphRuntime 与对应测试控制面存储。
    """

    registry = GraphRegistry()
    registry.register(definition)
    resolved_store = (
        checkpoint_store if checkpoint_store is not None else CapturingCheckpointStore()
    )
    runtime = DefaultGraphRuntime(
        checkpoint_store=resolved_store,
        checkpointer=InMemorySaver(),
        graph_registry=registry,
        settings=settings
        or GraphRuntimeSettings(
            graph_id=definition.graph_id,
            graph_version=definition.graph_version,
        ),
    )
    return runtime, resolved_store


def test_langgraph_runtime_rejects_undeclared_conditional_route() -> None:
    """验证非法条件路由由 LangGraph 条件边适配器映射为稳定错误。

    :return: None。
    """

    runtime, checkpoint_store = build_runtime_for_definition(
        GraphDefinition(
            graph_id="invalid_route_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="start",
            nodes={
                "start": GraphNodeSpec(node_id="start", handler=InvalidRouteNode()),
                "allowed": GraphNodeSpec(node_id="allowed", handler=SegmentNode()),
            },
            edges=(
                GraphEdgeSpec(
                    from_node="start",
                    to_node="allowed",
                    kind="conditional",
                ),
            ),
        )
    )

    with pytest.raises(GraphRuntimeError) as error_info:
        asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))

    assert error_info.value.code == GraphRuntimeErrorCode.GRAPH_NODE_NOT_FOUND
    assert len(checkpoint_store.release_calls) == 1
    assert checkpoint_store.save_calls == []


def test_langgraph_runtime_publishes_segments_once_per_stream() -> None:
    """验证 GraphRuntime 从 LangGraph updates 中发布 segment 并记录幂等状态。

    :return: None。
    """

    runtime, checkpoint_store = build_runtime_for_definition(
        GraphDefinition(
            graph_id="segment_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="segment",
            nodes={
                "segment": GraphNodeSpec(node_id="segment", handler=SegmentNode()),
            },
        )
    )

    events = asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))
    segment_event_types = [
        event.event_type
        for event in events
        if event.data.get("segment_id") == "segment_run_1_one"
    ]

    assert segment_event_types == [
        GraphRuntimeEventType.SEGMENT_READY.value,
        GraphRuntimeEventType.SEGMENT_COMPLETED.value,
        GraphRuntimeEventType.SEGMENT_PUBLISHED.value,
    ]
    assert len(checkpoint_store.publish_calls) == 1
    assert checkpoint_store.publish_calls[0].task_id == "task_segment"
    assert checkpoint_store.publish_calls[0].metadata["node_id"] == "segment"
    assert checkpoint_store.save_calls == []


def test_langgraph_runtime_releases_lock_when_node_fails() -> None:
    """验证节点失败时 GraphRuntime 仍会释放项目运行锁。

    :return: None。
    """

    runtime, checkpoint_store = build_runtime_for_definition(
        GraphDefinition(
            graph_id="failing_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="boom",
            nodes={
                "boom": GraphNodeSpec(
                    node_id="boom",
                    handler=FailingNode(),
                    max_attempts=1,
                ),
            },
        )
    )

    with pytest.raises(GraphRuntimeError) as error_info:
        asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))

    assert error_info.value.code == GraphRuntimeErrorCode.GRAPH_NODE_FAILED
    assert len(checkpoint_store.acquire_calls) == 1
    assert len(checkpoint_store.release_calls) == 1


def test_langgraph_runtime_can_suppress_node_events() -> None:
    """验证关闭节点事件开关后仍保留运行、checkpoint 和 segment 事件。

    :return: None。
    """

    runtime, _checkpoint_store = build_runtime_for_definition(
        GraphDefinition(
            graph_id="quiet_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.test",
            entry_node="segment",
            nodes={
                "segment": GraphNodeSpec(node_id="segment", handler=SegmentNode()),
            },
        ),
        settings=GraphRuntimeSettings(
            graph_id="quiet_graph",
            graph_version="v1",
            emit_node_events=False,
        ),
    )

    events = asyncio.run(collect_events(runtime.stream_turn(build_graph_request())))
    event_types = [event.event_type for event in events]

    assert GraphRuntimeEventType.NODE_STARTED.value not in event_types
    assert GraphRuntimeEventType.NODE_COMPLETED.value not in event_types
    assert GraphRuntimeEventType.CHECKPOINT_SAVED.value in event_types
    assert GraphRuntimeEventType.SEGMENT_PUBLISHED.value in event_types
    assert GraphRuntimeEventType.RUN_COMPLETED.value in event_types
