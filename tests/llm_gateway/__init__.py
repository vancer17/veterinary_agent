##################################################################################################
# 文件: tests/llm_gateway/__init__.py
# 作用: 作为 LlmGateway 组件测试包统一出口，集中暴露跨测试文件复用的 fake、构造器与收集工具。
# 边界: 仅暴露测试辅助契约；不连接真实模型代理、不实现 AgentRunner、LogicTraceStore 或其他领域能力。
##################################################################################################

from .helpers import (
    FakeProviderAdapter,
    RaisingTraceStore,
    RecordingTraceStore,
    build_invocation_request,
    build_success_response,
    build_test_settings,
    collect_stream_events,
)

__all__: tuple[str, ...] = (
    "FakeProviderAdapter",
    "RaisingTraceStore",
    "RecordingTraceStore",
    "build_invocation_request",
    "build_success_response",
    "build_test_settings",
    "collect_stream_events",
)
