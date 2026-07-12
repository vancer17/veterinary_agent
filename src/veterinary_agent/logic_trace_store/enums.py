##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/enums.py
# 作用: 定义 LogicTraceStore 组件稳定字符串枚举，供 DTO、错误映射、SQL 存储和端口适配复用。
# 边界: 仅承载通用逻辑链留痕枚举，不定义 L2 兽医业务 trace schema 或具体业务事件类型。
##################################################################################################

from enum import StrEnum


class LogicTraceErrorCode(StrEnum):
    """LogicTraceStore 稳定错误码。"""

    TRACE_NOT_FOUND = "TRACE_NOT_FOUND"
    TRACE_ALREADY_FINALIZED = "TRACE_ALREADY_FINALIZED"
    TRACE_EVENT_SCHEMA_INVALID = "TRACE_EVENT_SCHEMA_INVALID"
    TRACE_CAPTURE_POLICY_NOT_FOUND = "TRACE_CAPTURE_POLICY_NOT_FOUND"
    TRACE_ARTIFACT_UNAVAILABLE = "TRACE_ARTIFACT_UNAVAILABLE"
    TRACE_PROJECTION_BUILD_FAILED = "TRACE_PROJECTION_BUILD_FAILED"
    TRACE_STORAGE_WRITE_FAILED = "TRACE_STORAGE_WRITE_FAILED"
    TRACE_OUTBOX_WRITE_FAILED = "TRACE_OUTBOX_WRITE_FAILED"
    TRACE_STREAM_DELIVERY_FAILED = "TRACE_STREAM_DELIVERY_FAILED"
    TRACE_OPERATION_TIMEOUT = "TRACE_OPERATION_TIMEOUT"
    TRACE_INVALID_ARGUMENT = "TRACE_INVALID_ARGUMENT"
    TRACE_STORE_UNAVAILABLE = "TRACE_STORE_UNAVAILABLE"


class LogicTraceOperation(StrEnum):
    """LogicTraceStore 对外操作名。"""

    START_TRACE = "StartTrace"
    APPEND_TRACE_EVENT = "AppendTraceEvent"
    RECORD_CALL_SUMMARY = "RecordCallSummary"
    RECORD_TRACE_ARTIFACT = "RecordTraceArtifact"
    BUILD_TRACE_PROJECTION = "BuildTraceProjection"
    FINALIZE_TRACE = "FinalizeTrace"
    GET_TRACE = "GetTrace"
    LIST_TRACES = "ListTraces"


class LogicTraceStatus(StrEnum):
    """逻辑链生命周期状态。"""

    OPEN = "open"
    FINALIZED = "finalized"
    DEGRADED = "degraded"


class LogicTraceFinalStatus(StrEnum):
    """逻辑链最终状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERABLE = "recoverable"


class LogicTraceWriteStatus(StrEnum):
    """LogicTraceStore 通用写入状态。"""

    WRITTEN = "written"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class TraceCallType(StrEnum):
    """逻辑链调用摘要类型。"""

    MODEL = "model"
    TOOL = "tool"
    RAG = "rag"
    AGENT_RUN = "agent_run"
    POLICY_DECISION = "policy_decision"
    GRAPH_EVENT = "graph_event"


class TraceCallStatus(StrEnum):
    """逻辑链调用摘要状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TraceArtifactType(StrEnum):
    """逻辑链 artifact 类型。"""

    PROMPT_SUMMARY = "prompt_summary"
    OUTPUT_SUMMARY = "output_summary"
    RAG_SUMMARY = "rag_summary"
    DRAFT_RESPONSE = "draft_response"
    REVIEWED_DRAFT = "reviewed_draft"
    FINAL_RESPONSE = "final_response"
    OTHER = "other"


class TraceProjectionType(StrEnum):
    """逻辑链投影视图类型。"""

    TIMELINE = "timeline_view"
    DECISION = "decision_view"
    ARTIFACT = "artifact_view"
    REASONING_DISPLAY = "reasoning_display"


class TraceOutboxStatus(StrEnum):
    """逻辑链 outbox 记录状态。"""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


__all__: tuple[str, ...] = (
    "LogicTraceErrorCode",
    "LogicTraceFinalStatus",
    "LogicTraceOperation",
    "LogicTraceStatus",
    "LogicTraceWriteStatus",
    "TraceArtifactType",
    "TraceCallStatus",
    "TraceCallType",
    "TraceOutboxStatus",
    "TraceProjectionType",
)
