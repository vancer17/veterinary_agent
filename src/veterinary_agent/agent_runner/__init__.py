##################################################################################################
# 文件: src/veterinary_agent/agent_runner/__init__.py
# 作用: 作为 AgentRunner 组件包统一出口，集中暴露规格、DTO、枚举、错误、端口与默认实现。
# 边界: 外部包应从本文件导入 AgentRunner 公共契约，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.agent_runner.dto import (
    AgentPromptEstimateDto,
    AgentRetryPolicyDto,
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunSummaryDto,
    AgentRunnerDto,
    AgentRunnerErrorDto,
    AgentRunnerTraceWriteResultDto,
    AgentSpecDto,
    AgentTimeoutPolicyDto,
    AgentToolBindingResultDto,
    AgentToolCallSummaryDto,
    AgentToolPolicyDto,
    AgentTracePolicyDto,
    AgentUsageSummaryDto,
    AgentValidationErrorDto,
    JsonMap,
    PromptBlockDto,
)
from veterinary_agent.agent_runner.enums import (
    AgentResponseFormat,
    AgentRunStatus,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
    AgentRunnerTraceWriteStatus,
    AgentToolBindingStatus,
    AgentType,
)
from veterinary_agent.agent_runner.errors import (
    AgentRunnerError,
    build_agent_runner_error_dto,
    is_agent_runner_error_retryable_by_default,
)
from veterinary_agent.agent_runner.messages import LangChainMessageComposer
from veterinary_agent.agent_runner.parser import (
    DefaultStructuredOutputParser,
    StructuredOutputParseResult,
)
from veterinary_agent.agent_runner.ports import (
    AgentRunner,
    AgentRunnerTraceSink,
    AgentSpecRegistry,
    AgentToolRegistry,
    TODO_AGENT_RUNNER_TRACE_SINK_ERROR_CODE,
    TODO_AGENT_TOOL_REGISTRY_ERROR_CODE,
    TodoAgentRunnerTraceSink,
    TodoAgentToolRegistry,
)
from veterinary_agent.agent_runner.prompt import (
    DefaultPromptRenderer,
    PromptRenderResult,
)
from veterinary_agent.agent_runner.registry import InMemoryAgentSpecRegistry
from veterinary_agent.agent_runner.runner import (
    DefaultAgentRunner,
    create_default_agent_runner,
)

__all__: tuple[str, ...] = (
    "AgentPromptEstimateDto",
    "AgentResponseFormat",
    "AgentRetryPolicyDto",
    "AgentRunRequestDto",
    "AgentRunResultDto",
    "AgentRunStatus",
    "AgentRunSummaryDto",
    "AgentRunner",
    "AgentRunnerDto",
    "AgentRunnerError",
    "AgentRunnerErrorCode",
    "AgentRunnerErrorDto",
    "AgentRunnerOperation",
    "AgentRunnerTraceSink",
    "AgentRunnerTraceWriteResultDto",
    "AgentRunnerTraceWriteStatus",
    "AgentSpecDto",
    "AgentSpecRegistry",
    "AgentTimeoutPolicyDto",
    "AgentToolBindingResultDto",
    "AgentToolBindingStatus",
    "AgentToolCallSummaryDto",
    "AgentToolPolicyDto",
    "AgentToolRegistry",
    "AgentTracePolicyDto",
    "AgentType",
    "AgentUsageSummaryDto",
    "AgentValidationErrorDto",
    "DefaultAgentRunner",
    "DefaultPromptRenderer",
    "DefaultStructuredOutputParser",
    "InMemoryAgentSpecRegistry",
    "JsonMap",
    "LangChainMessageComposer",
    "PromptBlockDto",
    "PromptRenderResult",
    "StructuredOutputParseResult",
    "TODO_AGENT_RUNNER_TRACE_SINK_ERROR_CODE",
    "TODO_AGENT_TOOL_REGISTRY_ERROR_CODE",
    "TodoAgentRunnerTraceSink",
    "TodoAgentToolRegistry",
    "build_agent_runner_error_dto",
    "create_default_agent_runner",
    "is_agent_runner_error_retryable_by_default",
)
