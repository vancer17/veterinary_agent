##################################################################################################
# 文件: tests/agent_runner/test_prompt_renderer.py
# 作用: 验证 AgentRunner prompt 渲染集成，包括 Jinja2 模板、旧模板兼容与变量白名单边界。
# 边界: 不测试 Jinja2 自身语法完备性、不读取业务上下文来源、不实现 VetContextBuilder。
##################################################################################################

import asyncio

from veterinary_agent.agent_runner import AgentRunStatus, AgentRunnerErrorCode
from tests.llm_gateway import build_success_response

from .helpers import (
    AgentRunnerFixture,
    build_agent_runner_fixture,
    build_agent_runner_request,
    build_agent_runner_spec,
)


def _captured_system_prompt(fixture: AgentRunnerFixture) -> str:
    """读取 fake provider 捕获到的首条 system prompt。

    :param fixture: AgentRunner 组件测试夹具。
    :return: fake provider 收到的首条消息文本。
    """

    assert fixture.adapter.invoke_requests
    content = fixture.adapter.invoke_requests[0].messages[0].content
    assert isinstance(content, str)
    return content


def test_jinja_prompt_renders_blocks_and_task_input() -> None:
    """验证 Jinja2 prompt 可渲染上下文块与任务输入。

    :return: None。
    """

    spec = build_agent_runner_spec()
    spec.prompt_template = (
        "Agent={{ agent_id }}@{{ agent_version }}\n"
        "{% for block in prompt_block_items %}"
        "BLOCK={{ block.block_type }}:{{ block.block_id }}\n"
        "TEXT={{ block.content_ref_or_text }}\n"
        "{% endfor %}"
        "INPUT={{ task_input_json }}\n"
    )
    fixture = build_agent_runner_fixture(
        spec=spec,
        outcomes=[build_success_response(content='{"result": "ok"}')],
    )
    request = build_agent_runner_request(run_id="run_prompt_jinja")

    result = asyncio.run(fixture.runner.run_agent(request))
    rendered = _captured_system_prompt(fixture)

    assert result.status is AgentRunStatus.SUCCEEDED
    assert "Agent=standard_consultation_agent@v1" in rendered
    assert "BLOCK=vet_context:context_001" in rendered
    assert "宠物为猫，体重 4kg" in rendered
    assert "chief_complaint" in rendered


def test_legacy_prompt_field_syntax_remains_compatible() -> None:
    """验证旧版 ``{field}`` prompt 模板语法仍可兼容渲染。

    :return: None。
    """

    fixture = build_agent_runner_fixture(
        outcomes=[build_success_response(content='{"result": "legacy"}')],
    )
    request = build_agent_runner_request(run_id="run_prompt_legacy")

    result = asyncio.run(fixture.runner.run_agent(request))
    rendered = _captured_system_prompt(fixture)

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.parsed_output["result"] == "legacy"
    assert "上下文块" in rendered
    assert "任务输入" in rendered
    assert "runtime_options" not in rendered
    assert "temperature" in rendered


def test_prompt_rejects_unknown_template_variable() -> None:
    """验证 prompt 模板包含未授权变量时明确失败。

    :return: None。
    """

    spec = build_agent_runner_spec()
    spec.prompt_template = "非法变量：{{ unsafe_context }}"
    fixture = build_agent_runner_fixture(
        spec=spec,
        outcomes=[build_success_response(content='{"result": "unused"}')],
    )
    request = build_agent_runner_request(run_id="run_prompt_unknown")

    result = asyncio.run(fixture.runner.run_agent(request))

    assert result.status is AgentRunStatus.FAILED
    assert result.error is not None
    assert result.error.code is AgentRunnerErrorCode.PROMPT_RENDER_FAILED
    assert fixture.adapter.invoke_requests == []
