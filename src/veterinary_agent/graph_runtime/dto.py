##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/dto.py
# 作用: 定义 GraphRuntime 设置、运行身份、thread 控制面上下文和 LangGraph 恢复引用。
# 边界: 仅承载 GraphRuntime 自身数据；不实现图编译、checkpoint 访问或兽医业务逻辑。
##################################################################################################

from dataclasses import dataclass
from typing import TypeAlias

JsonMap: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True)
class GraphRuntimeSettings:
    """GraphRuntime 运行设置。

    配置项均由当前实现直接消费，不额外引入尚未接入的配置文件字段。
    """

    graph_id: str = "vet_conversation_graph"
    graph_version: str = "v2-langgraph"
    run_lock_ttl_seconds: float = 60.0
    run_deadline_seconds: float = 60.0
    default_node_timeout_seconds: float = 30.0
    default_node_max_attempts: int = 1
    retry_initial_interval_seconds: float = 0.25
    retry_backoff_factor: float = 2.0
    retry_max_interval_seconds: float = 4.0
    retry_jitter: bool = True
    emit_node_events: bool = True
    durability: str = "sync"

    def __post_init__(self) -> None:
        """校验 GraphRuntime 运行设置。

        :return: None。
        :raises ValueError: 当配置字段为空或数值范围非法时抛出。
        """

        if not self.graph_id.strip():
            raise ValueError("graph_id 不得为空")
        if not self.graph_version.strip():
            raise ValueError("graph_version 不得为空")
        if self.run_lock_ttl_seconds <= 0:
            raise ValueError("run_lock_ttl_seconds 必须大于 0")
        if self.run_deadline_seconds <= 0:
            raise ValueError("run_deadline_seconds 必须大于 0")
        if self.default_node_timeout_seconds <= 0:
            raise ValueError("default_node_timeout_seconds 必须大于 0")
        if self.default_node_max_attempts <= 0:
            raise ValueError("default_node_max_attempts 必须大于 0")
        if self.retry_initial_interval_seconds <= 0:
            raise ValueError("retry_initial_interval_seconds 必须大于 0")
        if self.retry_backoff_factor < 1:
            raise ValueError("retry_backoff_factor 必须大于或等于 1")
        if self.retry_max_interval_seconds <= 0:
            raise ValueError("retry_max_interval_seconds 必须大于 0")
        if self.durability not in {"sync", "async", "exit"}:
            raise ValueError("durability 必须为 sync、async 或 exit")


@dataclass(frozen=True, slots=True)
class GraphResumeRef:
    """LangGraph 恢复引用。"""

    thread_id: str
    checkpoint_id: str | None = None


@dataclass(slots=True)
class GraphRunControlContext:
    """单次图运行的项目控制面上下文。"""

    request_id: str
    trace_id: str
    run_id: str
    session_id: str
    user_id: str
    pet_id: str
    thread_id: str
    lock_acquired: bool = False


@dataclass(frozen=True, slots=True)
class GraphRunIdentity:
    """GraphRuntime 标准事件使用的运行身份。"""

    request_id: str
    trace_id: str
    run_id: str
    graph_id: str
    graph_version: str
    state_schema_version: str
    params_version: str
    config_snapshot_id: str


def parse_graph_checkpoint_ref(checkpoint_ref: str) -> GraphResumeRef:
    """解析 ``thread_id`` 或 ``thread_id/checkpoint_id`` 形式的恢复引用。

    :param checkpoint_ref: GraphRuntime 对外恢复引用。
    :return: 已解析的 LangGraph thread 与可选 checkpoint ID。
    :raises ValueError: 当恢复引用为空、格式非法或包含多余路径段时抛出。
    """

    normalized_ref = checkpoint_ref.strip().strip("/")
    if not normalized_ref:
        raise ValueError("checkpoint_ref 不得为空")
    parts = normalized_ref.split("/")
    if len(parts) == 1 and parts[0]:
        return GraphResumeRef(thread_id=parts[0])
    if len(parts) == 2 and all(parts):
        return GraphResumeRef(thread_id=parts[0], checkpoint_id=parts[1])
    raise ValueError("checkpoint_ref 必须为 thread_id 或 thread_id/checkpoint_id")


__all__: tuple[str, ...] = (
    "GraphResumeRef",
    "GraphRunControlContext",
    "GraphRunIdentity",
    "GraphRuntimeSettings",
    "JsonMap",
    "parse_graph_checkpoint_ref",
)
