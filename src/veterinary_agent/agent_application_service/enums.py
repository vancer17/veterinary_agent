##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/enums.py
# 作用: 定义 AgentApplicationService 组件的稳定字符串枚举，供应用契约、错误映射、Trace 与运行状态复用。
# 边界: 仅描述应用编排层语义，不包含 HTTP 状态码、图节点业务语义或下游组件实现细节。
##################################################################################################

from enum import StrEnum


class AgentApplicationOperation(StrEnum):
    """AgentApplicationService 对外操作名。"""

    EXECUTE_TURN = "ExecuteTurn"
    STREAM_TURN = "StreamTurn"
    RESUME_TURN = "ResumeTurn"
    CANCEL_TURN = "CancelTurn"


class AgentApplicationPhase(StrEnum):
    """单轮 Agent 应用编排阶段。"""

    PREPARING = "preparing"
    TRACE_STARTING = "trace_starting"
    PET_SESSION_POLICY = "pet_session_policy"
    USER_MESSAGE_PERSISTING = "user_message_persisting"
    GRAPH_EXECUTING = "graph_executing"
    TRACE_FINALIZING = "trace_finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AgentApplicationErrorCode(StrEnum):
    """AgentApplicationService 稳定错误码。"""

    APPLICATION_NOT_READY = "AGENT_APPLICATION_NOT_READY"
    REQUIRED_CONTEXT_MISSING = "AGENT_REQUIRED_CONTEXT_MISSING"
    PET_SESSION_CONFLICT = "AGENT_PET_SESSION_CONFLICT"
    SESSION_IDENTITY_CONFLICT = "AGENT_SESSION_IDENTITY_CONFLICT"
    SESSION_CLOSED = "AGENT_SESSION_CLOSED"
    SESSION_ARCHIVED = "AGENT_SESSION_ARCHIVED"
    DEPENDENCY_UNAVAILABLE = "AGENT_DEPENDENCY_UNAVAILABLE"
    TRACE_START_FAILED = "AGENT_TRACE_START_FAILED"
    USER_MESSAGE_PERSIST_FAILED = "AGENT_USER_MESSAGE_PERSIST_FAILED"
    GRAPH_RUNTIME_UNAVAILABLE = "AGENT_GRAPH_RUNTIME_UNAVAILABLE"
    GRAPH_EXECUTION_TIMEOUT = "AGENT_GRAPH_EXECUTION_TIMEOUT"
    GRAPH_EXECUTION_FAILED = "AGENT_GRAPH_EXECUTION_FAILED"
    GRAPH_RESULT_INVALID = "AGENT_GRAPH_RESULT_INVALID"
    TURN_ALREADY_RUNNING = "AGENT_TURN_ALREADY_RUNNING"
    TURN_CANCELLED = "AGENT_TURN_CANCELLED"
    INTERNAL_ERROR = "AGENT_APPLICATION_INTERNAL_ERROR"


class AgentTurnStatus(StrEnum):
    """应用层单轮 Agent 执行状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentTraceDeliveryStatus(StrEnum):
    """应用层感知的逻辑链交付状态。"""

    WRITTEN = "written"
    DEGRADED = "degraded"
    FAILED = "failed"


class AgentTraceFinalStatus(StrEnum):
    """逻辑链最终状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERABLE = "recoverable"


__all__: tuple[str, ...] = (
    "AgentApplicationErrorCode",
    "AgentApplicationOperation",
    "AgentApplicationPhase",
    "AgentTraceDeliveryStatus",
    "AgentTraceFinalStatus",
    "AgentTurnStatus",
)
