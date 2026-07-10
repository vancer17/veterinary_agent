##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/enums.py
# 作用: 定义 LlmGateway 稳定字符串枚举，统一消息、响应、流式事件、错误与留痕状态。
# 边界: 仅承载协议无关枚举，不实现供应商适配、重试、降级、网络调用或业务安全判断。
##################################################################################################

from enum import StrEnum


class LlmMessageRole(StrEnum):
    """模型消息角色。"""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmContentPartType(StrEnum):
    """模型消息内容分片类型。"""

    TEXT = "text"
    IMAGE_URL = "image_url"


class LlmResponseFormatType(StrEnum):
    """模型响应格式类型。"""

    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class LlmFinishReason(StrEnum):
    """归一化模型完成原因。"""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    SAFETY = "safety"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class LlmStreamEventType(StrEnum):
    """归一化模型流式事件类型。"""

    STARTED = "llm.started"
    DELTA = "llm.delta"
    TOOL_CALL_DELTA = "llm.tool_call_delta"
    USAGE = "llm.usage"
    COMPLETED = "llm.completed"
    ERROR = "llm.error"


class ProviderStreamEventType(StrEnum):
    """ProviderAdapter 内部流式事件类型。"""

    DELTA = "provider.delta"
    TOOL_CALL_DELTA = "provider.tool_call_delta"
    USAGE = "provider.usage"
    COMPLETED = "provider.completed"


class LlmGatewayErrorCode(StrEnum):
    """LlmGateway 稳定错误码。"""

    LLM_GATEWAY_NOT_READY = "LLM_GATEWAY_NOT_READY"
    LLM_PROFILE_NOT_FOUND = "LLM_PROFILE_NOT_FOUND"
    LLM_PROFILE_UNAVAILABLE = "LLM_PROFILE_UNAVAILABLE"
    LLM_CAPABILITY_MISMATCH = "LLM_CAPABILITY_MISMATCH"
    LLM_CONTEXT_LENGTH_EXCEEDED = "LLM_CONTEXT_LENGTH_EXCEEDED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_FIRST_TOKEN_TIMEOUT = "LLM_FIRST_TOKEN_TIMEOUT"  # nosec B105
    LLM_PROXY_UNAVAILABLE = "LLM_PROXY_UNAVAILABLE"
    LLM_PROVIDER_UNAVAILABLE = "LLM_PROVIDER_UNAVAILABLE"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_INVALID_REQUEST = "LLM_INVALID_REQUEST"
    LLM_SAFETY_BLOCKED = "LLM_SAFETY_BLOCKED"
    LLM_MALFORMED_RESPONSE = "LLM_MALFORMED_RESPONSE"
    LLM_RETRY_EXHAUSTED = "LLM_RETRY_EXHAUSTED"
    LLM_CONCURRENCY_LIMITED = "LLM_CONCURRENCY_LIMITED"
    LLM_CANCELLED = "LLM_CANCELLED"


class LlmGatewayOperation(StrEnum):
    """LlmGateway 对外与内部稳定操作名。"""

    INVOKE_LLM = "InvokeLlm"
    STREAM_LLM = "StreamLlm"
    ESTIMATE_LLM_TOKENS = "EstimateLlmTokens"
    CHECK_MODEL_PROFILE = "CheckModelProfile"
    CHECK_PROVIDER_ROUTE_HEALTH = "CheckProviderRouteHealth"
    WRITE_CALL_SUMMARY = "WriteLlmCallSummary"


class LlmTraceWriteStatus(StrEnum):
    """模型调用摘要写入状态。"""

    DELIVERED = "delivered"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


__all__: tuple[str, ...] = (
    "LlmContentPartType",
    "LlmFinishReason",
    "LlmGatewayErrorCode",
    "LlmGatewayOperation",
    "LlmMessageRole",
    "LlmResponseFormatType",
    "LlmStreamEventType",
    "LlmTraceWriteStatus",
    "ProviderStreamEventType",
)
