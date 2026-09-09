"""
=============================================================================
文件：tests/test_core_llm_gateway.py
作用：验证统一模型网关的调用、重试、审计与失败显式化行为。
范围：使用脚本化传输端口，不访问真实 LiteLLM 或厂商服务。
说明：这些用例锁定“唯一模型入口 + Strict JSON + 显式失败”的基础契约。
=============================================================================
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from pydantic import SecretStr

from vet_agent.core import errors as core_errors
from vet_agent.core import llm, prompts, schema, testing
from vet_agent.core.execution import ExecutionContext


class Output(schema.StrictModel):
    """模型网关测试用的最小严格输出。

    :ivar value: 整数输出字段。
    """

    value: int


class FixedClock:
    """确定性单调时钟测试替身。

    :ivar current_ns: 当前纳秒时间戳。
    """

    def __init__(self) -> None:
        """初始化固定时钟。

        :return: 无返回值。
        """
        self.current_ns = 1_000_000

    def monotonic_ns(self) -> int:
        """返回并推进确定性时间。

        :return: 当前测试时间戳。
        """
        self.current_ns += 1_000_000
        return self.current_ns


class RecordingSleeper:
    """记录传输重试等待时长的测试替身。

    :ivar delays: 已发生的等待秒数列表。
    """

    def __init__(self) -> None:
        """初始化记录器。

        :return: 无返回值。
        """
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        """记录等待时长并立即返回。

        :param seconds: 网关请求等待的秒数。
        :return: 无返回值。
        """
        self.delays.append(seconds)


class RecordingEventSink:
    """记录模型审计事件的测试替身。

    :ivar events: 已收到的事件列表。
    """

    def __init__(self) -> None:
        """初始化事件记录器。

        :return: 无返回值。
        """
        self.events: list[llm.LLMCallEvent] = []

    async def emit(self, event: llm.LLMCallEvent) -> None:
        """保存一个模型调用事件。

        :param event: 模型网关生成的脱敏事件。
        :return: 无返回值。
        """
        self.events.append(event)


def _fixed_random_unit() -> float:
    """返回固定重试随机数。

    :return: 始终为 ``0.5``，用于稳定断言退避计算。
    """
    return 0.5


def _prompt() -> prompts.RenderedPrompt:
    """构造绑定测试 schema 的版本化 prompt。

    :return: 可传入模型网关的已渲染 prompt。
    """
    template = "输出 JSON"
    return prompts.RenderedPrompt(
        record=prompts.PromptRecord(
            prompt_id="test_prompt",
            prompt_version="1",
            prompt_sha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
            model_role=llm.ModelRole.ANCHOR_GENERATOR.value,
            output_schema=Output,
            template=template,
        ),
        messages=(prompts.PromptMessage(prompts.PromptMessageRole.USER, template),),
    )


def _settings(*, max_retries: int = 1) -> llm.LLMSettings:
    """构造只包含测试角色的模型配置。

    :param max_retries: 传输层最大额外尝试次数。
    :return: 不可变模型网关配置。
    """
    return llm.LLMSettings(
        base_url="http://litellm.local",
        api_key=SecretStr("test-key"),
        role_configs={
            llm.ModelRole.ANCHOR_GENERATOR: llm.ModelRoleConfig(
                model="openai/fake-model",
                max_tokens=128,
                timeout_seconds=1.0,
            )
        },
        max_transport_retries=max_retries,
        retry_initial_delay_seconds=0.1,
        retry_max_delay_seconds=0.2,
    )


def _gateway(
    *,
    transport: testing.ScriptedChatCompletionTransport,
    max_retries: int = 1,
) -> tuple[llm.DefaultLLMGateway, RecordingEventSink, RecordingSleeper]:
    """构造使用脚本传输端口的默认模型网关。

    :param transport: 可脚本化传输测试替身。
    :param max_retries: 传输层最大额外尝试次数。
    :return: 网关、事件记录器和重试等待记录器。
    """
    event_sink = RecordingEventSink()
    sleeper = RecordingSleeper()
    gateway = llm.DefaultLLMGateway(
        settings=_settings(max_retries=max_retries),
        transport=transport,
        strict_json_gateway=schema.StrictJSONGateway(),
        event_sink=event_sink,
        clock=FixedClock(),
        retry_sleeper=sleeper,
        random_unit=_fixed_random_unit,
    )
    return gateway, event_sink, sleeper


def _context() -> ExecutionContext:
    """构造模型调用测试上下文。

    :return: 含执行链与步骤名的执行上下文。
    """
    return ExecutionContext(
        execution_id="execution-1",
        correlation_id="correlation-1",
        step="anchor_generator",
    )


def test_gateway_parses_typed_output_and_emits_audit() -> None:
    """验证成功调用会返回 typed 输出并生成完成审计事件。

    :return: 无返回值；metadata 或审计字段缺失时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    transport.enqueue_completion(
        content='{"value": 7}', prompt_tokens=20, completion_tokens=8
    )
    gateway, event_sink, sleeper = _gateway(transport=transport)

    response = asyncio.run(
        gateway.complete_json(
            role=llm.ModelRole.ANCHOR_GENERATOR,
            prompt=_prompt(),
            output_schema=Output,
            context=_context(),
        )
    )

    assert response.output.value == 7
    assert response.metadata.prompt_tokens == 20
    assert response.metadata.completion_tokens == 8
    assert response.metadata.attempt_count == 1
    assert len(event_sink.events) == 1
    assert event_sink.events[0].status == llm.LLMCallStatus.COMPLETED
    assert event_sink.events[0].output_digest
    assert sleeper.delays == []


def test_gateway_retries_only_retryable_transport_error() -> None:
    """验证 429 会按策略重试且逻辑请求标识保持稳定。

    :return: 无返回值；重试次数、事件序列或请求标识不稳定时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    transport.enqueue_failure(
        core_errors.RateLimitError(
            error_code=core_errors.CoreErrorCode.RATE_LIMITED,
            message="rate limited",
            retryable=True,
        )
    )
    transport.enqueue_completion(content='{"value": 8}')
    gateway, event_sink, sleeper = _gateway(transport=transport, max_retries=1)

    response = asyncio.run(
        gateway.complete_json(
            role=llm.ModelRole.ANCHOR_GENERATOR,
            prompt=_prompt(),
            output_schema=Output,
            context=_context(),
        )
    )

    assert response.output.value == 8
    assert response.metadata.attempt_count == 2
    assert len(transport.requests) == 2
    assert len({request.request_id for request in transport.requests}) == 1
    assert len(sleeper.delays) == 1
    assert [event.status for event in event_sink.events] == [
        llm.LLMCallStatus.ATTEMPT_FAILED,
        llm.LLMCallStatus.COMPLETED,
    ]


def test_gateway_does_not_retry_schema_invalid_output() -> None:
    """验证严格 JSON 或 schema 失败不会触发传输重试。

    :return: 无返回值；发生额外模型请求或缺少失败审计时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    transport.enqueue_completion(content='{"value": "invalid"}')
    gateway, event_sink, _ = _gateway(transport=transport, max_retries=2)

    with pytest.raises(core_errors.SchemaValidationError):
        asyncio.run(
            gateway.complete_json(
                role=llm.ModelRole.ANCHOR_GENERATOR,
                prompt=_prompt(),
                output_schema=Output,
                context=_context(),
            )
        )

    assert len(transport.requests) == 1
    assert event_sink.events[-1].status == llm.LLMCallStatus.FAILED
    assert (
        event_sink.events[-1].error_code
        == core_errors.CoreErrorCode.SCHEMA_INVALID.value
    )


def test_gateway_exhausts_transport_retries_and_fails() -> None:
    """验证可重试传输错误耗尽预算后显式失败。

    :return: 无返回值；失败被吞没或尝试次数不正确时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    for _ in range(3):
        transport.enqueue_failure(
            core_errors.LLMTransportError(
                error_code=core_errors.CoreErrorCode.LLM_TRANSPORT_FAILED,
                message="transport failed",
                retryable=True,
            )
        )
    gateway, event_sink, sleeper = _gateway(transport=transport, max_retries=2)

    with pytest.raises(core_errors.LLMTransportError):
        asyncio.run(
            gateway.complete_json(
                role=llm.ModelRole.ANCHOR_GENERATOR,
                prompt=_prompt(),
                output_schema=Output,
                context=_context(),
            )
        )

    assert len(transport.requests) == 3
    assert len(sleeper.delays) == 2
    assert event_sink.events[-1].status == llm.LLMCallStatus.FAILED
    assert (
        event_sink.events[-1].error_code
        == core_errors.CoreErrorCode.LLM_TRANSPORT_FAILED.value
    )


def test_gateway_rejects_prompt_schema_mismatch() -> None:
    """验证 prompt 绑定的 schema 与调用 schema 不一致时配置失败。

    :return: 无返回值；未在模型请求前显式失败时测试失败。
    """
    transport = testing.ScriptedChatCompletionTransport()
    gateway, _, _ = _gateway(transport=transport)

    class Other(schema.StrictModel):
        """另一个用于绑定冲突测试的严格 schema。

        :ivar value: 字符串字段。
        """

        value: str

    with pytest.raises(core_errors.ConfigurationError):
        asyncio.run(
            gateway.complete_json(
                role=llm.ModelRole.ANCHOR_GENERATOR,
                prompt=_prompt(),
                output_schema=Other,
                context=_context(),
            )
        )

    assert transport.requests == []
