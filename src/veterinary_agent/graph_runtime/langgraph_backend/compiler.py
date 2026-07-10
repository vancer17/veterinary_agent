##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/langgraph_backend/compiler.py
# 作用: 将项目版本化 GraphDefinition 编译为 LangGraph CompiledStateGraph。
# 边界: 仅负责图结构、节点策略和状态适配；不管理 thread、运行锁、事件协议或业务依赖。
##################################################################################################

from collections.abc import Awaitable, Callable, Sequence
from typing import Hashable, TypeAlias, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy

from veterinary_agent.graph_runtime.definition import (
    GraphDefinition,
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphNodeSpec,
)
from veterinary_agent.graph_runtime.dto import GraphRuntimeSettings, JsonMap
from veterinary_agent.graph_runtime.enums import GraphRuntimeErrorCode
from veterinary_agent.graph_runtime.errors import GraphRuntimeError
from veterinary_agent.graph_runtime.langgraph_backend.state import (
    LangGraphRunContext,
    LangGraphRuntimeState,
    project_handler_state,
)

CompiledGraph: TypeAlias = CompiledStateGraph[
    LangGraphRuntimeState,
    LangGraphRunContext,
    LangGraphRuntimeState,
    LangGraphRuntimeState,
]
CompiledNode: TypeAlias = Callable[
    ...,
    Awaitable[LangGraphRuntimeState],
]
ConditionalRouter: TypeAlias = Callable[
    [LangGraphRuntimeState],
    Hashable | Sequence[Hashable],
]


def _build_node_context(
    *,
    runtime_context: LangGraphRunContext,
    definition: GraphDefinition,
    node_id: str,
) -> GraphNodeExecutionContext:
    """构建项目节点处理器使用的执行上下文。

    :param runtime_context: LangGraph 注入的不可变运行期上下文。
    :param definition: 当前版本化图定义。
    :param node_id: 当前执行节点 ID。
    :return: 项目稳定的节点执行上下文。
    """

    identity = runtime_context.identity
    return GraphNodeExecutionContext(
        request_id=identity.request_id,
        trace_id=identity.trace_id,
        run_id=identity.run_id,
        graph_id=definition.graph_id,
        graph_version=definition.graph_version,
        node_id=node_id,
        session_id=runtime_context.session_id,
        user_id=runtime_context.user_id,
        current_pet_id=runtime_context.current_pet_id,
        params_version=identity.params_version,
        config_snapshot_id=identity.config_snapshot_id,
    )


def _serialize_node_events(result: GraphNodeResult) -> tuple[JsonMap, ...]:
    """将项目节点事件转换为可 checkpoint 的 JSON 映射。

    :param result: 当前项目节点执行结果。
    :return: 可由 LangGraph reducer 聚合的事件映射元组。
    """

    return tuple(event.model_dump(mode="json") for event in result.events)


def _build_node_update(
    *,
    node_id: str,
    result: GraphNodeResult,
) -> LangGraphRuntimeState:
    """将项目节点结果转换为 LangGraph 状态更新。

    :param node_id: 当前完成节点 ID。
    :param result: 项目节点执行结果。
    :return: 可由 LangGraph channel reducer 确定性合并的状态更新。
    """

    update: LangGraphRuntimeState = {
        "business_state": dict(result.state_patch),
        "node_outputs": {node_id: dict(result.state_patch)},
        "completed_nodes": (node_id,),
    }
    if result.events:
        update["node_events"] = _serialize_node_events(result)
    if result.selected_next_nodes is not None:
        update["selected_routes"] = {
            node_id: list(result.selected_next_nodes),
        }
    return update


def _build_compiled_node(
    *,
    definition: GraphDefinition,
    node: GraphNodeSpec,
) -> CompiledNode:
    """构建单个 LangGraph 节点适配闭包。

    :param definition: 当前版本化图定义。
    :param node: 需要适配的项目节点定义。
    :return: 可注册到 ``StateGraph`` 的严格类型异步节点函数。
    """

    async def execute_node(
        state: LangGraphRuntimeState,
        *,
        runtime: Runtime[LangGraphRunContext],
    ) -> LangGraphRuntimeState:
        """执行项目节点并返回 LangGraph 状态更新。

        :param state: LangGraph 当前类型化状态。
        :param runtime: LangGraph 注入的运行期上下文。
        :return: 当前节点产生的类型化状态更新。
        """

        result = await node.handler(
            project_handler_state(state),
            _build_node_context(
                runtime_context=runtime.context,
                definition=definition,
                node_id=node.node_id,
            ),
        )
        return _build_node_update(node_id=node.node_id, result=result)

    return execute_node


def _build_conditional_router(
    *,
    definition: GraphDefinition,
    node_id: str,
) -> ConditionalRouter:
    """构建 LangGraph 条件边路由闭包。

    :param definition: 当前版本化图定义。
    :param node_id: 产生条件路由结果的节点 ID。
    :return: 从 checkpoint 状态读取后继节点选择的同步路由函数。
    """

    allowed_nodes = frozenset(definition.conditional_next_node_ids(node_id))

    def route_from_state(
        state: LangGraphRuntimeState,
    ) -> Hashable | Sequence[Hashable]:
        """从状态读取并校验节点选择的条件后继节点。

        :param state: LangGraph 当前类型化状态。
        :return: 一个或多个合法后继节点 ID；空选择返回 LangGraph 终点。
        :raises GraphRuntimeError: 当节点选择了未声明的条件后继节点时抛出。
        """

        selected_routes = state.get("selected_routes", {})
        raw_selection = selected_routes.get(node_id)
        selected_nodes = (
            tuple(item for item in raw_selection if isinstance(item, str))
            if isinstance(raw_selection, list)
            else ()
        )
        for selected_node in selected_nodes:
            if selected_node not in allowed_nodes:
                raise GraphRuntimeError(
                    code=GraphRuntimeErrorCode.GRAPH_NODE_NOT_FOUND,
                    message="GraphRuntime 节点选择了未声明的条件后继节点",
                    graph_id=definition.graph_id,
                    graph_version=definition.graph_version,
                    node_id=node_id,
                    retryable=False,
                    details={"selected_next_node": selected_node},
                )
        return selected_nodes if selected_nodes else END

    return route_from_state


class LangGraphCompiler:
    """GraphRuntime 的 LangGraph 图编译器。"""

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver[str],
        settings: GraphRuntimeSettings,
    ) -> None:
        """初始化 LangGraph 图编译器。

        :param checkpointer: 图状态唯一权威写入使用的 LangGraph checkpointer。
        :param settings: GraphRuntime 节点策略默认值。
        :return: None。
        """

        self._checkpointer = checkpointer
        self._settings = settings

    def compile(self, definition: GraphDefinition) -> CompiledGraph:
        """编译版本化项目图定义。

        :param definition: 需要编译的版本化项目图定义。
        :return: 已注入 checkpoint 的 LangGraph compiled graph。
        """

        builder = StateGraph(
            LangGraphRuntimeState,
            context_schema=LangGraphRunContext,
        )
        for node in definition.nodes.values():
            builder.add_node(
                node.node_id,
                _build_compiled_node(definition=definition, node=node),
                retry_policy=self._build_retry_policy(node),
                timeout=(
                    node.timeout_seconds
                    if node.timeout_seconds is not None
                    else self._settings.default_node_timeout_seconds
                ),
            )
        builder.add_edge(START, definition.entry_node)
        self._add_static_edges(builder=builder, definition=definition)
        for node_id in definition.nodes:
            conditional_nodes = definition.conditional_next_node_ids(node_id)
            if conditional_nodes:
                path_map: dict[Hashable, str] = {
                    selected_node: selected_node for selected_node in conditional_nodes
                }
                path_map[END] = END
                builder.add_conditional_edges(
                    node_id,
                    _build_conditional_router(
                        definition=definition,
                        node_id=node_id,
                    ),
                    path_map,
                )
            if not definition.outgoing_edges(node_id):
                builder.add_edge(node_id, END)
        compiled = builder.compile(
            checkpointer=self._checkpointer,
            name=f"{definition.graph_id}:{definition.graph_version}",
        )
        return cast(CompiledGraph, compiled)

    def _build_retry_policy(self, node: GraphNodeSpec) -> RetryPolicy:
        """构建节点级 LangGraph 重试策略。

        :param node: 当前项目节点定义。
        :return: 已应用节点覆盖值和 GraphRuntime 默认值的重试策略。
        """

        return RetryPolicy(
            initial_interval=self._settings.retry_initial_interval_seconds,
            backoff_factor=self._settings.retry_backoff_factor,
            max_interval=self._settings.retry_max_interval_seconds,
            max_attempts=(
                node.max_attempts
                if node.max_attempts is not None
                else self._settings.default_node_max_attempts
            ),
            jitter=self._settings.retry_jitter,
        )

    def _add_static_edges(
        self,
        *,
        builder: StateGraph[LangGraphRuntimeState, LangGraphRunContext],
        definition: GraphDefinition,
    ) -> None:
        """将项目静态边注册到 LangGraph，保留多前驱 fan-in 语义。

        :param builder: 正在构建的 LangGraph StateGraph。
        :param definition: 当前版本化图定义。
        :return: None。
        """

        predecessors_by_target: dict[str, list[str]] = {}
        for edge in definition.edges:
            if edge.kind != "static":
                continue
            predecessors_by_target.setdefault(edge.to_node, []).append(edge.from_node)
        for target_node, predecessors in predecessors_by_target.items():
            if len(predecessors) == 1:
                builder.add_edge(predecessors[0], target_node)
            else:
                builder.add_edge(predecessors, target_node)


__all__: tuple[str, ...] = (
    "CompiledGraph",
    "LangGraphCompiler",
)
