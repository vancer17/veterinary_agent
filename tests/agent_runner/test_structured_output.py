##################################################################################################
# 文件: tests/agent_runner/test_structured_output.py
# 作用: 验证 AgentRunner 结构化输出解析、JSON Schema 校验与格式修复重试契约。
# 边界: 不测试 jsonschema 或 LangChain parser 内部实现，不修改业务语义或安全判定。
##################################################################################################

import asyncio

from veterinary_agent.agent_runner import AgentRunStatus, AgentRunnerErrorCode
from tests.llm_gateway import build_success_response

from .helpers import (
    build_agent_runner_fixture,
    build_agent_runner_request,
    build_agent_runner_spec,
)


def test_markdown_json_output_is_parsed() -> None:
    """验证 markdown fenced JSON 输出可解析为结构化结果。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        outcomes=[
            build_success_response(
                content='```json\n{"result": "from_markdown"}\n```',
            )
        ],
    )
    request = build_agent_runner_request(run_id="run_markdown_json")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.schema_valid is True
    assert result.parsed_output["result"] == "from_markdown"


def test_non_json_output_fails_without_format_repair() -> None:
    """验证非 JSON 输出在不允许修复时返回解析失败。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        spec=build_agent_runner_spec(max_format_repair_attempts=0),
        outcomes=[build_success_response(content="这不是 JSON")],
    )
    request = build_agent_runner_request(run_id="run_non_json")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.OUTPUT_PARSE_FAILED
    assert len(fixture.adapter.invoke_requests) == 1


def test_schema_invalid_output_fails_without_repair() -> None:
    """验证 schema 校验失败且不允许修复时返回标准错误。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        spec=build_agent_runner_spec(max_format_repair_attempts=0),
        outcomes=[build_success_response(content='{"result": 123}')],
    )
    request = build_agent_runner_request(run_id="run_schema_invalid")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.OUTPUT_SCHEMA_VALIDATION_FAILED
    assert len(fixture.adapter.invoke_requests) == 1


def test_format_repair_retry_sends_repair_instruction() -> None:
    """验证格式修复重试会追加修复指令并最终成功。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        spec=build_agent_runner_spec(max_format_repair_attempts=1),
        outcomes=[
            build_success_response(content='{"result": 123}'),
            build_success_response(content='{"result": "repaired"}'),
        ],
    )
    request = build_agent_runner_request(run_id="run_repair_instruction")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.retry_count == 1
    assert result.parsed_output["result"] == "repaired"
    assert len(fixture.adapter.invoke_requests) == 2
    repair_content = fixture.adapter.invoke_requests[1].messages[-1].content
    assert isinstance(repair_content, str)
    assert "请只修复上一条输出的格式" in repair_content
    assert "不要改变业务判断" in repair_content


def test_format_repair_retry_exhaustion_fails() -> None:
    """验证格式修复重试耗尽时返回重试耗尽错误。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        spec=build_agent_runner_spec(max_format_repair_attempts=1),
        outcomes=[
            build_success_response(content='{"result": 123}'),
            build_success_response(content='{"result": 456}'),
        ],
    )
    request = build_agent_runner_request(run_id="run_repair_exhausted")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.AGENT_RETRY_EXHAUSTED
    assert len(fixture.adapter.invoke_requests) == 2
