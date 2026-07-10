##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/enums.py
# 作用: 定义 GraphRuntime 组件的稳定字符串枚举，供事件、错误、运行状态与节点状态复用。
# 边界: 仅承载通用图运行时枚举，不包含节点调度、checkpoint、业务图或 L2 领域判断实现。
##################################################################################################

from enum import StrEnum


class GraphRuntimeEventType(StrEnum):
    """GraphRuntime 标准运行事件类型。"""

    RUN_STARTED = "graph.run_started"
    RUN_COMPLETED = "graph.run_completed"
    RUN_FAILED = "graph.run_failed"
    RUN_CANCELLED = "graph.run_cancelled"
    RUN_INTERRUPTED = "graph.run_interrupted"
    RESUME_STARTED = "graph.resume_started"
    CHECKPOINT_LOADED = "graph.checkpoint_loaded"
    CHECKPOINT_SAVED = "graph.checkpoint_saved"
    NODE_STARTED = "graph.node_started"
    NODE_COMPLETED = "graph.node_completed"
    NODE_FAILED = "graph.node_failed"
    NODE_RETRYING = "graph.node_retrying"
    SEGMENT_READY = "graph.segment_ready"
    SEGMENT_COMPLETED = "graph.segment_completed"
    SEGMENT_PUBLISHED = "graph.segment_published"
    DEGRADED = "graph.degraded"


class GraphRuntimeErrorCode(StrEnum):
    """GraphRuntime 稳定错误码。"""

    GRAPH_RUNTIME_NOT_READY = "GRAPH_RUNTIME_NOT_READY"
    GRAPH_DEFINITION_NOT_FOUND = "GRAPH_DEFINITION_NOT_FOUND"
    GRAPH_VERSION_UNAVAILABLE = "GRAPH_VERSION_UNAVAILABLE"
    GRAPH_CHECKPOINT_UNAVAILABLE = "GRAPH_CHECKPOINT_UNAVAILABLE"
    GRAPH_CHECKPOINT_REF_INVALID = "GRAPH_CHECKPOINT_REF_INVALID"
    GRAPH_RUN_LOCK_FAILED = "GRAPH_RUN_LOCK_FAILED"
    GRAPH_RUN_TIMEOUT = "GRAPH_RUN_TIMEOUT"
    GRAPH_NODE_NOT_FOUND = "GRAPH_NODE_NOT_FOUND"
    GRAPH_NODE_TIMEOUT = "GRAPH_NODE_TIMEOUT"
    GRAPH_NODE_FAILED = "GRAPH_NODE_FAILED"
    GRAPH_NODE_RETRY_EXHAUSTED = "GRAPH_NODE_RETRY_EXHAUSTED"
    GRAPH_RUN_CANCELLED = "GRAPH_RUN_CANCELLED"
    GRAPH_STATE_INVALID = "GRAPH_STATE_INVALID"


class GraphRunStatus(StrEnum):
    """GraphRuntime 图运行生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    RECOVERABLE = "recoverable"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GraphNodeStatus(StrEnum):
    """GraphRuntime 节点执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


__all__: tuple[str, ...] = (
    "GraphNodeStatus",
    "GraphRunStatus",
    "GraphRuntimeErrorCode",
    "GraphRuntimeEventType",
)
