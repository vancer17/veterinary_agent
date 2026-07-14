##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/definition.py
# 作用: 定义 GraphRuntime 对外稳定的版本化图描述、节点、边和节点处理器契约。
# 边界: 仅描述项目图元数据；实际调度、并行、重试、checkpoint 与恢复全部交由 LangGraph 后端。
##################################################################################################

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias

from veterinary_agent.agent_application_service import AgentGraphEventDto
from veterinary_agent.graph_runtime.dto import JsonMap
from veterinary_agent.graph_runtime.enums import GraphRuntimeErrorCode
from veterinary_agent.graph_runtime.errors import GraphRuntimeError

GraphState: TypeAlias = dict[str, object]
GraphEdgeKind: TypeAlias = Literal["static", "conditional"]


@dataclass(frozen=True, slots=True)
class GraphNodeResult:
    """图节点执行结果。

    ``state_patch`` 会由 LangGraph 兼容节点适配器写入类型化运行状态。新业务节点应把结构化结果放入
    ``state_patch``，并通过 ``selected_next_nodes`` 表达条件路由；不得自行调度后继节点。
    """

    state_patch: JsonMap = field(default_factory=dict)
    events: tuple[AgentGraphEventDto, ...] = ()
    selected_next_nodes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class GraphNodeExecutionContext:
    """图节点执行上下文。"""

    request_id: str
    trace_id: str
    run_id: str
    graph_id: str
    graph_version: str
    node_id: str
    session_id: str
    user_id: str
    current_pet_id: str
    params_version: str
    config_snapshot_id: str
    thread_id: str | None = None


class GraphNodeHandler(Protocol):
    """GraphRuntime 节点处理器协议。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """执行单个图节点。

        :param state: 当前图运行状态只读快照。
        :param context: 当前节点执行上下文。
        :return: 节点状态更新、领域事件和可选路由结果。
        """

        ...


@dataclass(frozen=True, slots=True)
class GraphNodeSpec:
    """版本化图中的节点定义。"""

    node_id: str
    handler: GraphNodeHandler
    timeout_seconds: float | None = None
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        """校验图节点定义。

        :return: None。
        :raises ValueError: 当节点 ID 为空或节点策略非法时抛出。
        """

        if not self.node_id.strip():
            raise ValueError("node_id 不得为空")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.max_attempts is not None and self.max_attempts <= 0:
            raise ValueError("max_attempts 必须大于 0")


@dataclass(frozen=True, slots=True)
class GraphEdgeSpec:
    """版本化图中的边定义。"""

    from_node: str
    to_node: str
    kind: GraphEdgeKind = "static"

    def __post_init__(self) -> None:
        """校验图边定义。

        :return: None。
        :raises ValueError: 当边端点为空或边类型非法时抛出。
        """

        if not self.from_node.strip():
            raise ValueError("from_node 不得为空")
        if not self.to_node.strip():
            raise ValueError("to_node 不得为空")
        if self.kind not in {"static", "conditional"}:
            raise ValueError("kind 必须为 static 或 conditional")


@dataclass(frozen=True, slots=True)
class GraphDefinition:
    """可编译为 LangGraph ``StateGraph`` 的版本化图描述。"""

    graph_id: str
    graph_version: str
    state_schema_version: str
    entry_node: str
    nodes: dict[str, GraphNodeSpec]
    edges: tuple[GraphEdgeSpec, ...] = ()

    def __post_init__(self) -> None:
        """校验版本化图定义。

        :return: None。
        :raises ValueError: 当图标识、入口节点或边引用非法时抛出。
        """

        if not self.graph_id.strip():
            raise ValueError("graph_id 不得为空")
        if not self.graph_version.strip():
            raise ValueError("graph_version 不得为空")
        if not self.state_schema_version.strip():
            raise ValueError("state_schema_version 不得为空")
        if not self.entry_node.strip():
            raise ValueError("entry_node 不得为空")
        if self.entry_node not in self.nodes:
            raise ValueError("entry_node 必须存在于 nodes 中")
        for node_id, node in self.nodes.items():
            if node_id != node.node_id:
                raise ValueError("nodes 字典键必须与 GraphNodeSpec.node_id 一致")
        for edge in self.edges:
            if edge.from_node not in self.nodes:
                raise ValueError(f"边起点节点不存在: {edge.from_node}")
            if edge.to_node not in self.nodes:
                raise ValueError(f"边终点节点不存在: {edge.to_node}")
        for node_id in self.nodes:
            edge_kinds = {edge.kind for edge in self.outgoing_edges(node_id)}
            if len(edge_kinds) > 1:
                raise ValueError("同一节点不得同时声明 static 与 conditional 出边")

    def get_node(self, node_id: str) -> GraphNodeSpec:
        """读取指定节点定义。

        :param node_id: 需要读取的节点 ID。
        :return: 命中的节点定义。
        :raises GraphRuntimeError: 当节点不存在时抛出。
        """

        node = self.nodes.get(node_id)
        if node is not None:
            return node
        raise GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_NODE_NOT_FOUND,
            message="GraphRuntime 图节点不存在",
            graph_id=self.graph_id,
            graph_version=self.graph_version,
            node_id=node_id,
            retryable=False,
        )

    def outgoing_edges(self, node_id: str) -> tuple[GraphEdgeSpec, ...]:
        """读取指定节点的全部出边。

        :param node_id: 当前节点 ID。
        :return: 当前节点的出边元组。
        """

        return tuple(edge for edge in self.edges if edge.from_node == node_id)

    def static_next_node_ids(self, node_id: str) -> tuple[str, ...]:
        """读取指定节点的静态后继节点。

        :param node_id: 当前节点 ID。
        :return: 静态后继节点 ID 元组。
        """

        return tuple(
            edge.to_node
            for edge in self.edges
            if edge.from_node == node_id and edge.kind == "static"
        )

    def conditional_next_node_ids(self, node_id: str) -> tuple[str, ...]:
        """读取指定节点允许选择的条件后继节点。

        :param node_id: 当前节点 ID。
        :return: 条件后继节点 ID 元组。
        """

        return tuple(
            edge.to_node
            for edge in self.edges
            if edge.from_node == node_id and edge.kind == "conditional"
        )

    def terminal_node_ids(self) -> tuple[str, ...]:
        """读取没有任何出边的终态节点。

        :return: 终态节点 ID 元组。
        """

        return tuple(
            node_id for node_id in self.nodes if not self.outgoing_edges(node_id)
        )


__all__: tuple[str, ...] = (
    "GraphDefinition",
    "GraphEdgeKind",
    "GraphEdgeSpec",
    "GraphNodeExecutionContext",
    "GraphNodeHandler",
    "GraphNodeResult",
    "GraphNodeSpec",
    "GraphState",
)
