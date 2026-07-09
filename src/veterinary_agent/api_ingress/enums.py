##################################################################################################
# 文件: src/veterinary_agent/api_ingress/enums.py
# 作用: 定义 API 接入组件使用的稳定字符串枚举模型，供后续 DTO、错误映射、SSE 映射、日志和指标复用。
# 边界: 仅包含 ApiIngress 协议接入层枚举，不包含兽医业务路由、安全判决、模型策略或 L1/L2 组件枚举。
##################################################################################################

from enum import StrEnum


class ResponseMode(StrEnum):
    """入口响应模式。"""

    SYNC = "sync"
    STREAM = "stream"


class ApiRouteKind(StrEnum):
    """API 接入路由类型。"""

    AGENT_TURNS = "agent_turns"
    OPENAI_RESPONSES = "openai_responses"
    HEALTH = "health"
    READY = "ready"


class IngressErrorCode(StrEnum):
    """API 接入层错误码。"""

    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    ORCHESTRATOR_TIMEOUT = "ORCHESTRATOR_TIMEOUT"
    CLIENT_CANCELLED = "CLIENT_CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class SseEventType(StrEnum):
    """SSE 流式事件类型。"""

    TURN_STARTED = "turn.started"
    REASONING_DISPLAY_STARTED = "reasoning_display.started"
    REASONING_DISPLAY_DELTA = "reasoning_display.delta"
    REASONING_DISPLAY_COMPLETED = "reasoning_display.completed"
    SEGMENT_STARTED = "segment.started"
    SEGMENT_DELTA = "segment.delta"
    SEGMENT_COMPLETED = "segment.completed"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    HEARTBEAT = "heartbeat"


class TurnStatus(StrEnum):
    """单轮请求生命周期状态。"""

    COMPLETED = "completed"
    FAILED = "failed"


class SegmentStatus(StrEnum):
    """业务分段发布状态。"""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class InputRole(StrEnum):
    """外部输入消息角色。"""

    USER = "user"


class InputItemType(StrEnum):
    """外部输入项类型。"""

    MESSAGE = "message"


class InputContentType(StrEnum):
    """外部输入内容项类型。"""

    INPUT_TEXT = "input_text"
    INPUT_ATTACHMENT = "input_attachment"


class OutputContentType(StrEnum):
    """输出内容项类型。"""

    OUTPUT_TEXT = "output_text"
    OUTPUT_TEXT_DELTA = "output_text_delta"


__all__: tuple[str, ...] = (
    "ApiRouteKind",
    "IngressErrorCode",
    "InputContentType",
    "InputItemType",
    "InputRole",
    "OutputContentType",
    "ResponseMode",
    "SegmentStatus",
    "SseEventType",
    "TurnStatus",
)
