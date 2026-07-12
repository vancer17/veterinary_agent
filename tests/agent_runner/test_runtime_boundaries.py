##################################################################################################
# 文件: tests/agent_runner/test_runtime_boundaries.py
# 作用: 验证 AgentRunner 运行期边界，包括 LlmGateway token 权威、工具调用边界、trace 降级与就绪状态。
# 边界: 不实现真实 ToolRegistry、VetContextBuilder、ConversationStore、RAG 或业务图调度。
##################################################################################################

import asyncio
from pathlib import Path

from veterinary_agent.agent_runner import (
    AgentRunStatus,
    AgentRunnerErrorCode,
    AgentRunnerTraceWriteStatus,
)
from tests.llm_gateway import build_success_response, build_test_settings

from .helpers import (
    RaisingAgentRunnerTraceSink,
    RecordingAgentRunnerTraceSink,
    build_agent_runner_fixture,
    build_agent_runner_request,
    build_tool_call_response,
)


def _agent_runner_source_paths() -> list[Path]:
    """读取 AgentRunner 组件源码文件路径。

    :return: AgentRunner 组件包下的 Python 源码文件列表。
    """

    project_root = Path(__file__).resolve().parents[2]
    package_root = project_root / "src" / "veterinary_agent" / "agent_runner"
    return sorted(package_root.glob("*.py"))


def test_context_limit_is_mapped_without_agent_runner_trimming() -> None:
    """验证上下文超限由 LlmGateway 判定且 AgentRunner 不裁剪重构。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        settings=build_test_settings(max_context_tokens=256),
        outcomes=[build_success_response(content='{"result": "should_not_call"}')],
    )
    request = build_agent_runner_request(
        run_id="run_context_boundary",
        content="x" * 5000,
    )

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.TOKEN_BUDGET_EXCEEDED
    assert fixture.adapter.invoke_requests == []


def test_model_tool_calls_are_rejected_until_tool_loop_exists() -> None:
    """验证模型返回工具调用时 AgentRunner 明确失败。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        outcomes=[build_tool_call_response()],
    )
    request = build_agent_runner_request(run_id="run_tool_call_response")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.TOOL_EXECUTION_FAILED
    assert result.error.conflict_with is not None
    assert result.error.conflict_with["tool_call_count"] == 1
    assert len(fixture.adapter.invoke_requests) == 1


def test_trace_sink_degraded_status_is_returned() -> None:
    """验证 trace sink 主动降级状态会返回到 AgentRunner 结果。

    :return: None。
    """

    trace_sink = RecordingAgentRunnerTraceSink(
        status=AgentRunnerTraceWriteStatus.DEGRADED,
    )
    fixture = build_agent_runner_fixture(trace_sink=trace_sink)
    request = build_agent_runner_request(run_id="run_trace_degraded")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.trace_delivery_status is AgentRunnerTraceWriteStatus.DEGRADED
    assert len(trace_sink.summaries) == 1


def test_trace_sink_exception_degrades_without_failing_run() -> None:
    """验证 trace sink 抛异常时不影响主运行成功。

    :return: None。
    """

    trace_sink = RaisingAgentRunnerTraceSink(RuntimeError("trace unavailable"))
    fixture = build_agent_runner_fixture(trace_sink=trace_sink)
    request = build_agent_runner_request(run_id="run_trace_exception")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.trace_delivery_status is AgentRunnerTraceWriteStatus.DEGRADED


def test_agent_runner_readiness_follows_lifecycle_and_llm_gateway() -> None:
    """验证 AgentRunner 就绪状态跟随生命周期和 LlmGateway 状态。

    :return: None。
    """

    ready_fixture = build_agent_runner_fixture()
    not_ready_fixture = build_agent_runner_fixture(adapter_ready=False)

    assert ready_fixture.runner.is_ready() is True
    assert not_ready_fixture.runner.is_ready() is False

    asyncio.run(ready_fixture.runner.close())

    assert ready_fixture.runner.is_ready() is False


def test_agent_runner_package_does_not_import_l2_context_or_storage_domains() -> None:
    """验证 AgentRunner 包不直接引用上下文、会话、记忆或 RAG 领域实现。

    :return: None。
    """

    forbidden_imports = (
        "veterinary_agent.conversation_store",
        "veterinary_agent.vet_context_builder",
        "veterinary_agent.rag_platform",
        "veterinary_agent.vet_memory_service",
        "trim_messages",
    )

    for source_path in _agent_runner_source_paths():
        source_text = source_path.read_text(encoding="utf-8")
        for forbidden_import in forbidden_imports:
            assert forbidden_import not in source_text, (
                f"{source_path} 不应直接引用 {forbidden_import}"
            )
