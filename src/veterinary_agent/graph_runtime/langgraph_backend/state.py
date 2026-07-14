##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/langgraph_backend/state.py
# 作用: 定义 LangGraph 执行内核使用的类型化状态、运行期上下文与确定性 reducer。
# 边界: 仅承载通用图运行数据；不实现节点业务、项目控制面或协议事件映射。
##################################################################################################

from dataclasses import dataclass
from typing import Annotated, TypedDict

from veterinary_agent.graph_runtime.definition import GraphState
from veterinary_agent.graph_runtime.dto import GraphRunIdentity, JsonMap


def merge_json_maps(
    left: JsonMap | None,
    right: JsonMap | None,
) -> JsonMap:
    """合并两个 JSON 映射。

    :param left: reducer 当前已聚合的左侧映射。
    :param right: 本次节点返回的右侧映射。
    :return: 右侧同名字段覆盖左侧后的新映射。
    """

    return {**(left or {}), **(right or {})}


def append_string_tuple(
    left: tuple[str, ...] | None,
    right: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """追加字符串元组并按首次出现顺序去重。

    :param left: reducer 当前已聚合的左侧字符串元组。
    :param right: 本次节点返回的右侧字符串元组。
    :return: 按首次出现顺序去重后的字符串元组。
    """

    return tuple(dict.fromkeys((*(left or ()), *(right or ()))))


def append_json_map_tuple(
    left: tuple[JsonMap, ...] | None,
    right: tuple[JsonMap, ...] | None,
) -> tuple[JsonMap, ...]:
    """追加 JSON 映射元组。

    :param left: reducer 当前已聚合的左侧映射元组。
    :param right: 本次节点返回的右侧映射元组。
    :return: 保持 LangGraph superstep 合并顺序的映射元组。
    """

    return *(left or ()), *(right or ())


class LangGraphRuntimeState(TypedDict, total=False):
    """LangGraph 编排内核持久化的类型化图状态。"""

    request: JsonMap
    identity: JsonMap
    business_state: Annotated[JsonMap, merge_json_maps]
    node_outputs: Annotated[JsonMap, merge_json_maps]
    completed_nodes: Annotated[tuple[str, ...], append_string_tuple]
    node_events: Annotated[tuple[JsonMap, ...], append_json_map_tuple]
    selected_routes: Annotated[JsonMap, merge_json_maps]


@dataclass(frozen=True, slots=True)
class LangGraphRunContext:
    """通过 LangGraph ``Runtime`` 注入节点的不可变运行期上下文。"""

    identity: GraphRunIdentity
    session_id: str
    user_id: str
    current_pet_id: str
    request: JsonMap
    thread_id: str | None = None


def project_handler_state(state: LangGraphRuntimeState) -> GraphState:
    """将 LangGraph 内部状态投影为兼容节点可读取的状态视图。

    :param state: LangGraph 当前类型化状态。
    :return: 兼容 ``GraphNodeHandler`` 的扁平只读状态副本。
    """

    projected: GraphState = dict(state.get("business_state", {}))
    projected["request"] = dict(state.get("request", {}))
    projected["identity"] = dict(state.get("identity", {}))
    projected["node_outputs"] = dict(state.get("node_outputs", {}))
    projected["completed_nodes"] = list(state.get("completed_nodes", ()))
    return projected


__all__: tuple[str, ...] = (
    "LangGraphRunContext",
    "LangGraphRuntimeState",
    "append_json_map_tuple",
    "append_string_tuple",
    "merge_json_maps",
    "project_handler_state",
)
