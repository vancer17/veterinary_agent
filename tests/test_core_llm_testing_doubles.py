"""
=============================================================================
文件：tests/test_core_llm_testing_doubles.py
作用：验证 FakeLLM 与脚本传输端口自身的核心契约。
范围：不访问外部服务，也不测试语义输出质量。
说明：确保测试替身保持 prompt 绑定检查、typed 输出与严格解析能力。
=============================================================================
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from vet_agent.core import llm, prompts, schema, testing
from vet_agent.core.errors import StrictJSONValidationError
from vet_agent.core.execution import ExecutionContext


class Output(schema.StrictModel):
    """Fake LLM 测试用严格输出。

    :ivar value: 整数字段。
    """

    value: int


def _prompt() -> prompts.RenderedPrompt:
    """构造 Fake LLM 调用所需的版本化 prompt。

    :return: 与测试角色和 schema 绑定的 prompt。
    """
    template = "测试 prompt"
    return prompts.RenderedPrompt(
        record=prompts.PromptRecord(
            prompt_id="fake_prompt",
            prompt_version="1",
            prompt_sha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
            model_role=llm.ModelRole.ANCHOR_GENERATOR.value,
            output_schema=Output,
            template=template,
        ),
        messages=(prompts.PromptMessage(prompts.PromptMessageRole.USER, template),),
    )


def _context() -> ExecutionContext:
    """构造 Fake LLM 测试上下文。

    :return: 测试执行上下文。
    """
    return ExecutionContext(
        execution_id="fake-execution",
        correlation_id=None,
        step="fake_step",
    )


def test_fake_llm_gateway_parses_raw_content() -> None:
    """验证 FakeLLM 可通过 Strict JSON Gateway 消费原始文本。

    :return: 无返回值；typed 输出或调用记录不正确时测试失败。
    """
    fake = testing.FakeLLMGateway()
    fake.enqueue(testing.FakeLLMResult.raw_content('{"value": 3}'))

    response = asyncio.run(
        fake.complete_json(
            role=llm.ModelRole.ANCHOR_GENERATOR,
            prompt=_prompt(),
            output_schema=Output,
            context=_context(),
        )
    )

    assert response.output.value == 3
    assert fake.calls[0].prompt_id == "fake_prompt"
    assert fake.calls[0].execution_id == "fake-execution"


def test_fake_llm_gateway_preserves_invalid_json_failure() -> None:
    """验证 FakeLLM 不宽松修复非法 JSON。

    :return: 无返回值；非法输出被转换成功时测试失败。
    """
    fake = testing.FakeLLMGateway()
    fake.enqueue(testing.FakeLLMResult.raw_content("not-json"))

    with pytest.raises(StrictJSONValidationError):
        asyncio.run(
            fake.complete_json(
                role=llm.ModelRole.ANCHOR_GENERATOR,
                prompt=_prompt(),
                output_schema=Output,
                context=_context(),
            )
        )


def test_scripted_transport_records_requests() -> None:
    """验证脚本传输端口记录物理请求且可注入异常。

    :return: 无返回值；请求快照或异常传播不正确时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    transport.enqueue_completion(content='{"value": 1}')
    failure = RuntimeError(" scripted failure")
    transport.enqueue_failure(failure)

    first = asyncio.run(
        transport.complete(
            llm.ChatCompletionRequest(
                request_id="request-1",
                attempt_id="attempt-1",
                model="fake-model",
                model_role=llm.ModelRole.ANCHOR_GENERATOR.value,
                prompt_id="fake_prompt",
                prompt_version="1",
                prompt_sha256=_prompt().record.prompt_sha256,
                messages=(
                    prompts.PromptMessage(prompts.PromptMessageRole.USER, "输入"),
                ),
                temperature=0.0,
                max_tokens=10,
                timeout_seconds=1.0,
                response_format=llm.StrictResponseFormat.JSON_OBJECT,
            )
        )
    )
    with pytest.raises(RuntimeError, match="scripted failure"):
        first = asyncio.run(
            transport.complete(
                llm.ChatCompletionRequest(
                    request_id="request-2",
                    attempt_id="attempt-2",
                    model="fake-model",
                    model_role=llm.ModelRole.ANCHOR_GENERATOR.value,
                    prompt_id="fake_prompt",
                    prompt_version="1",
                    prompt_sha256=_prompt().record.prompt_sha256,
                    messages=(
                        prompts.PromptMessage(prompts.PromptMessageRole.USER, "输入"),
                    ),
                    temperature=0.0,
                    max_tokens=10,
                    timeout_seconds=1.0,
                    response_format=llm.StrictResponseFormat.JSON_OBJECT,
                )
            )
        )

    assert first.content == '{"value": 1}'
    assert [request.request_id for request in transport.requests] == [
        "request-1",
        "request-2",
    ]
    assert {request.prompt_id for request in transport.requests} == {"fake_prompt"}
    assert {request.prompt_version for request in transport.requests} == {"1"}
