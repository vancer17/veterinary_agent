##################################################################################################
# 文件: tests/agent_runner/test_component_contract.py
# 作用: 验证 AgentRunner 公共契约、prompt 估算、成功运行、格式修复重试与工具权限边界。
# 边界: 不访问真实模型代理、不执行网络请求、不实现业务图编排。
##################################################################################################

import asyncio

from veterinary_agent.agent_runner import (
    AgentRunStatus,
    AgentRunnerErrorCode,
)
from tests.llm_gateway import build_success_response, build_test_settings

from .helpers import (
    build_agent_runner_request,
    build_agent_runner_spec,
    build_default_agent_runner,
)


def test_agent_runner_success_flow() -> None:
    """验证 AgentRunner 可完成一次标准成功运行。

    :return: None。
    """

    runner, _, trace_sink = build_default_agent_runner()
    request = build_agent_runner_request()

    result = asyncio.run(runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.schema_valid is True
    assert result.parsed_output["result"] == "ok"
    assert result.retry_count == 0
    assert result.trace_delivery_status.value == "delivered"
    assert len(trace_sink.summaries) == 1
    assert trace_sink.summaries[0].agent_id == request.agent_id


def test_agent_runner_repairs_invalid_schema_output() -> None:
    """验证 AgentRunner 会对结构化输出 schema 失败执行有限格式修复。

    :return: None。
    """

    invalid_content = '{"result": 123}'
    valid_content = '{"result": "repaired"}'
    runner, _, trace_sink = build_default_agent_runner(
        spec=build_agent_runner_spec(max_format_repair_attempts=1),
        outcomes=[
            build_success_response(content=invalid_content),
            build_success_response(content=valid_content),
        ],
    )
    request = build_agent_runner_request(run_id="run_repair")

    result = asyncio.run(runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.retry_count == 1
    assert result.schema_valid is True
    assert result.parsed_output["result"] == "repaired"
    assert len(trace_sink.summaries) == 1


def test_agent_runner_estimate_prompt() -> None:
    """验证 AgentRunner 可以在调用前估算 prompt token 预算。

    :return: None。
    """

    runner, _, _ = build_default_agent_runner()
    request = build_agent_runner_request(run_id="run_estimate")

    estimate = runner.estimate_agent_prompt(request)

    assert estimate.agent_id == request.agent_id
    assert estimate.model_profile == "profile_primary"
    assert estimate.input_tokens > 0
    assert estimate.total_budget_tokens <= estimate.max_context_tokens


def test_agent_runner_supports_jinja_prompt_and_markdown_json_output() -> None:
    """验证 AgentRunner 支持 Jinja2 prompt 与 markdown JSON 输出解析。

    :return: None。
    """

    spec = build_agent_runner_spec()
    spec.prompt_template = (
        "你是 {{ agent_id }}@{{ agent_version }}。\n"
        "{% for block in prompt_block_items %}"
        "[{{ block.block_type }}:{{ block.block_id }}]\n"
        "{{ block.content_ref_or_text }}\n"
        "{% endfor %}"
        "任务输入：{{ task_input_json }}\n"
    )
    runner, _, _ = build_default_agent_runner(
        spec=spec,
        outcomes=[
            build_success_response(
                content='```json\n{"result": "from_jinja"}\n```',
            )
        ],
    )
    request = build_agent_runner_request(run_id="run_jinja")

    result = asyncio.run(runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.schema_valid is True
    assert result.parsed_output["result"] == "from_jinja"


def test_agent_runner_maps_llm_gateway_context_limit_without_trimming() -> None:
    """验证 AgentRunner 不裁剪业务上下文并映射 LlmGateway 超窗错误。

    :return: None。
    """

    runner, _, trace_sink = build_default_agent_runner(
        settings=build_test_settings(max_context_tokens=256),
        outcomes=[build_success_response(content='{"result": "should_not_call"}')],
    )
    request = build_agent_runner_request(
        run_id="run_context_limit",
        content="x" * 5000,
    )

    result = asyncio.run(runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.TOKEN_BUDGET_EXCEEDED
    assert result.parsed_output == {}
    assert result.trace_delivery_status.value == "delivered"
    assert len(trace_sink.summaries) == 1


def test_agent_runner_rejects_tool_declaration_without_tool_registry() -> None:
    """验证声明工具权限但未接入 ToolRegistry 时会明确失败。

    :return: None。
    """

    runner, _, trace_sink = build_default_agent_runner(
        spec=build_agent_runner_spec(allowed_tools=["fetch_lab_result"]),
    )
    request = build_agent_runner_request(run_id="run_tool_missing")

    result = asyncio.run(runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.TOOL_EXECUTION_FAILED
    assert result.trace_delivery_status.value == "delivered"
    assert len(trace_sink.summaries) == 1
