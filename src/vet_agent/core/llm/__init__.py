"""
统一模型调用基础设施包。

语义层必须通过本包暴露的 ``LLMGateway`` / ``DefaultLLMGateway`` 使用模型，
不得直接创建厂商 SDK client 或感知 LiteLLM 适配细节。
"""

from vet_agent.core.llm.budget import BudgetLease, LLMBudget
from vet_agent.core.llm.concurrency import LLMConcurrencyLimiter, LLMConcurrencySlot
from vet_agent.core.llm.contracts import (
    ChatCompletionClient,
    ChatCompletionRequest,
    LLMCallEvent,
    LLMCallStatus,
    LLMEventSink,
    LLMGateway,
    LLMResponse,
    LLMResponseMetadata,
    LLMSettings,
    LLMUsage,
    ModelRole,
    ModelRoleConfig,
    NormalizedChatCompletion,
    NullLLMEventSink,
    StrictResponseFormat,
)
from vet_agent.core.llm.gateway import (
    DefaultLLMGateway,
    MonotonicClock,
    SystemMonotonicClock,
    UUIDLLMIdGenerator,
)
from vet_agent.core.llm.litellm_client import LiteLLMChatCompletionClient
from vet_agent.core.llm.retry import (
    DefaultTransportRetrySleeper,
    TransportRetryPolicy,
    TransportRetrySleeper,
    calculate_transport_delay,
)

__all__ = [
    "BudgetLease",
    "ChatCompletionClient",
    "ChatCompletionRequest",
    "DefaultLLMGateway",
    "DefaultTransportRetrySleeper",
    "LLMBudget",
    "LLMCallEvent",
    "LLMCallStatus",
    "LLMConcurrencyLimiter",
    "LLMConcurrencySlot",
    "LLMEventSink",
    "LLMGateway",
    "LLMResponse",
    "LLMResponseMetadata",
    "LLMSettings",
    "LLMUsage",
    "LiteLLMChatCompletionClient",
    "ModelRole",
    "ModelRoleConfig",
    "MonotonicClock",
    "NormalizedChatCompletion",
    "NullLLMEventSink",
    "StrictResponseFormat",
    "SystemMonotonicClock",
    "TransportRetryPolicy",
    "TransportRetrySleeper",
    "UUIDLLMIdGenerator",
    "calculate_transport_delay",
]
