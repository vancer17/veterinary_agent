##################################################################################################
# 文件: tests/agent_runner/__init__.py
# 作用: 作为 AgentRunner 组件测试包统一出口，集中暴露跨测试文件复用的 helper 构造器与 fake。
# 边界: 仅暴露测试辅助契约；不连接真实模型代理、不实现业务组件或其他领域能力。
##################################################################################################

from .helpers import (
    AgentRunnerFixture,
    RaisingAgentRunnerTraceSink,
    RecordingAgentRunnerTraceSink,
    build_agent_runner_fixture,
    build_agent_runner_request,
    build_agent_runner_spec,
    build_default_agent_runner,
    build_tool_call_response,
)

__all__: tuple[str, ...] = (
    "AgentRunnerFixture",
    "RaisingAgentRunnerTraceSink",
    "RecordingAgentRunnerTraceSink",
    "build_agent_runner_fixture",
    "build_agent_runner_request",
    "build_agent_runner_spec",
    "build_default_agent_runner",
    "build_tool_call_response",
)
