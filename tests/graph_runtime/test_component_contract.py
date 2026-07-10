##################################################################################################
# 文件: tests/graph_runtime/test_component_contract.py
# 作用: 验证 GraphRuntime 组件公开契约、图定义约束、注册表编译状态和恢复引用解析。
# 边界: 不执行真实业务图，不连接数据库；仅覆盖 GraphRuntime L1 组件的稳定公共接口。
##################################################################################################

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from veterinary_agent import (
    GraphDefinition,
    GraphEdgeSpec,
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphNodeSpec,
    GraphRegistry,
    GraphRuntimeError,
    GraphRuntimeErrorCode,
    GraphRuntimeSettings,
    GraphState,
    LangGraphCompiler,
    parse_graph_checkpoint_ref,
)


class NoopNode:
    """测试用无副作用节点。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """返回空状态补丁。

        :param state: 当前图状态。
        :param context: 当前节点上下文。
        :return: 空节点执行结果。
        """

        del state, context
        return GraphNodeResult()


def build_single_node_definition(
    *,
    graph_id: str = "contract_graph",
    graph_version: str = "v1",
) -> GraphDefinition:
    """构建单节点测试图定义。

    :param graph_id: 图定义 ID。
    :param graph_version: 图定义版本。
    :return: 单节点 GraphDefinition。
    """

    node = GraphNodeSpec(node_id="start", handler=NoopNode())
    return GraphDefinition(
        graph_id=graph_id,
        graph_version=graph_version,
        state_schema_version="graph_runtime.contract",
        entry_node="start",
        nodes={"start": node},
    )


def test_graph_definition_rejects_mixed_static_and_conditional_edges() -> None:
    """验证同一节点不能同时声明静态边和条件边。

    :return: None。
    """

    with pytest.raises(ValueError, match="static 与 conditional"):
        GraphDefinition(
            graph_id="invalid_graph",
            graph_version="v1",
            state_schema_version="graph_runtime.contract",
            entry_node="start",
            nodes={
                "start": GraphNodeSpec(node_id="start", handler=NoopNode()),
                "left": GraphNodeSpec(node_id="left", handler=NoopNode()),
                "right": GraphNodeSpec(node_id="right", handler=NoopNode()),
            },
            edges=(
                GraphEdgeSpec(from_node="start", to_node="left", kind="static"),
                GraphEdgeSpec(from_node="start", to_node="right", kind="conditional"),
            ),
        )


def test_graph_registry_requires_langgraph_compilation_before_get_compiled() -> None:
    """验证注册表在编译前不会返回 compiled graph。

    :return: None。
    """

    registry = GraphRegistry()
    registry.register(build_single_node_definition())

    with pytest.raises(GraphRuntimeError) as error_info:
        registry.get_compiled(graph_id="contract_graph", graph_version="v1")

    assert error_info.value.code == GraphRuntimeErrorCode.GRAPH_RUNTIME_NOT_READY
    assert (
        registry.has_graph(
            graph_id="contract_graph",
            graph_version="v1",
            require_compiled=True,
        )
        is False
    )


def test_graph_registry_compiles_registered_graphs_with_langgraph() -> None:
    """验证注册表可通过 LangGraphCompiler 编译全部已注册图。

    :return: None。
    """

    registry = GraphRegistry()
    registry.register(build_single_node_definition())

    registry.compile_all(
        LangGraphCompiler(
            checkpointer=InMemorySaver(),
            settings=GraphRuntimeSettings(),
        )
    )

    compiled = registry.get_compiled(graph_id="contract_graph", graph_version="v1")
    assert compiled is not None
    assert (
        registry.has_graph(
            graph_id="contract_graph",
            graph_version="v1",
            require_compiled=True,
        )
        is True
    )


def test_graph_registry_reports_missing_graph_and_version_with_stable_codes() -> None:
    """验证注册表对缺失图和缺失版本返回稳定错误码。

    :return: None。
    """

    registry = GraphRegistry()
    registry.register(build_single_node_definition())

    with pytest.raises(GraphRuntimeError) as missing_graph_info:
        registry.get_definition(graph_id="missing_graph", graph_version="v1")
    with pytest.raises(GraphRuntimeError) as missing_version_info:
        registry.get_definition(graph_id="contract_graph", graph_version="v2")

    assert (
        missing_graph_info.value.code
        == GraphRuntimeErrorCode.GRAPH_DEFINITION_NOT_FOUND
    )
    assert (
        missing_version_info.value.code
        == GraphRuntimeErrorCode.GRAPH_VERSION_UNAVAILABLE
    )


def test_parse_graph_checkpoint_ref_supports_thread_and_checkpoint() -> None:
    """验证恢复引用支持 thread 与指定 checkpoint 两种形式。

    :return: None。
    """

    latest_ref = parse_graph_checkpoint_ref(" thread_1 ")
    checkpoint_ref = parse_graph_checkpoint_ref("/thread_1/checkpoint_1/")

    assert latest_ref.thread_id == "thread_1"
    assert latest_ref.checkpoint_id is None
    assert checkpoint_ref.thread_id == "thread_1"
    assert checkpoint_ref.checkpoint_id == "checkpoint_1"


def test_parse_graph_checkpoint_ref_rejects_invalid_ref() -> None:
    """验证恢复引用会拒绝空值和多余路径段。

    :return: None。
    """

    for checkpoint_ref in ("", "thread_1/checkpoint_1/extra", "thread_1//bad"):
        with pytest.raises(ValueError):
            parse_graph_checkpoint_ref(checkpoint_ref)


def test_graph_runtime_settings_rejects_invalid_values() -> None:
    """验证 GraphRuntime 设置拒绝悬空或非法配置值。

    :return: None。
    """

    with pytest.raises(ValueError, match="graph_id"):
        GraphRuntimeSettings(graph_id="")
    with pytest.raises(ValueError, match="default_node_max_attempts"):
        GraphRuntimeSettings(default_node_max_attempts=0)
    with pytest.raises(ValueError, match="durability"):
        GraphRuntimeSettings(durability="memory-only")
