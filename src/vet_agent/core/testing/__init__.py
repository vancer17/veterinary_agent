"""
核心基础设施测试替身包。

该包提供可脚本化的模型传输客户端和 Fake LLM 网关，用于覆盖正常输出、
协议错误、严格 JSON 错误、schema 错误和传输失败等关键契约场景。
"""

from vet_agent.core.testing.fake_llm import (
    FakeLLMGateway,
    FakeLLMResult,
    RecordedLLMCall,
)
from vet_agent.core.testing.transport import ScriptedChatCompletionTransport

__all__ = [
    "FakeLLMGateway",
    "FakeLLMResult",
    "RecordedLLMCall",
    "ScriptedChatCompletionTransport",
]
