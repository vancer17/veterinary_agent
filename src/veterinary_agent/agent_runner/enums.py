##################################################################################################
# 文件: src/veterinary_agent/agent_runner/enums.py
# 作用: 定义 AgentRunner 组件稳定字符串枚举，覆盖 Agent 类型、运行状态、响应格式、错误码与操作名。
# 边界: 仅承载协议无关枚举，不执行 prompt 渲染、模型调用、工具绑定、结构化解析或业务安全判断。
##################################################################################################

from enum import StrEnum


class AgentType(StrEnum):
    """Agent 规格类型。"""

    GENERIC = "generic"
    INPUT_SAFETY = "input_safety"
    STANDARD = "standard"
    EDUCATION = "education"
    NONMEDICAL = "nonmedical"
    SAFETY_TRIGGER = "safety_trigger"
    OUTPUT_SAFETY_REVIEW = "output_safety_review"


class AgentResponseFormat(StrEnum):
    """Agent 期望的模型响应格式。"""

    AUTO = "auto"
    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class AgentRunStatus(StrEnum):
    """AgentRunner 单次运行状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentRunnerErrorCode(StrEnum):
    """AgentRunner 稳定错误码。"""

    AGENT_RUNNER_NOT_READY = "AGENT_RUNNER_NOT_READY"
    AGENT_SPEC_NOT_FOUND = "AGENT_SPEC_NOT_FOUND"
    AGENT_SPEC_VERSION_UNAVAILABLE = "AGENT_SPEC_VERSION_UNAVAILABLE"
    AGENT_RUN_REQUEST_INVALID = "AGENT_RUN_REQUEST_INVALID"
    PROMPT_RENDER_FAILED = "PROMPT_RENDER_FAILED"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"  # nosec B105
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_PROVIDER_ERROR = "MODEL_PROVIDER_ERROR"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    OUTPUT_PARSE_FAILED = "OUTPUT_PARSE_FAILED"
    OUTPUT_SCHEMA_VALIDATION_FAILED = "OUTPUT_SCHEMA_VALIDATION_FAILED"
    AGENT_RETRY_EXHAUSTED = "AGENT_RETRY_EXHAUSTED"
    AGENT_CANCELLED = "AGENT_CANCELLED"


class AgentRunnerOperation(StrEnum):
    """AgentRunner 对外与内部稳定操作名。"""

    RUN_AGENT = "RunAgent"
    RUN_AGENT_WITH_EVENTS = "RunAgentWithEvents"
    VALIDATE_AGENT_SPEC = "ValidateAgentSpec"
    ESTIMATE_AGENT_PROMPT = "EstimateAgentPrompt"
    RESOLVE_AGENT_SPEC = "ResolveAgentSpec"
    RENDER_PROMPT = "RenderAgentPrompt"
    BIND_TOOLS = "BindAgentTools"
    PARSE_OUTPUT = "ParseAgentOutput"
    VALIDATE_OUTPUT_SCHEMA = "ValidateAgentOutputSchema"
    WRITE_RUN_SUMMARY = "WriteAgentRunSummary"


class AgentRunnerTraceWriteStatus(StrEnum):
    """AgentRunner 运行摘要写入状态。"""

    DELIVERED = "delivered"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class AgentToolBindingStatus(StrEnum):
    """Agent 工具绑定状态。"""

    BOUND = "bound"
    SKIPPED = "skipped"
    DEGRADED = "degraded"


__all__: tuple[str, ...] = (
    "AgentResponseFormat",
    "AgentRunStatus",
    "AgentRunnerErrorCode",
    "AgentRunnerOperation",
    "AgentRunnerTraceWriteStatus",
    "AgentToolBindingStatus",
    "AgentType",
)
