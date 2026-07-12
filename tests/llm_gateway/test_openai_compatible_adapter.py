##################################################################################################
# 文件: tests/llm_gateway/test_openai_compatible_adapter.py
# 作用: 验证 OpenAI-compatible 适配器的请求转换、非流式响应解析、SSE 解析和 HTTP 错误映射。
# 边界: 使用 httpx MockTransport，不访问真实 one-api、LiteLLM、New API 或外部模型供应商。
##################################################################################################

import asyncio
import json

import httpx
import pytest

from veterinary_agent.llm_gateway import (
    LlmGatewayError,
    LlmGatewayErrorCode,
    LlmMessageDto,
    LlmMessageRole,
    LlmResponseFormatDto,
    OpenAICompatibleAdapter,
    ProviderInvocationRequestDto,
    ProviderStreamEventDto,
    ProviderStreamEventType,
)
from veterinary_agent.config import (
    LlmProviderRouteConfig,
    LlmTimeoutPolicyConfig,
)


def _provider_request(*, stream: bool = False) -> ProviderInvocationRequestDto:
    """构建测试用 ProviderAdapter 物理请求。

    :param stream: 是否构建流式请求。
    :return: ProviderInvocationRequestDto。
    """

    return ProviderInvocationRequestDto(
        call_id="llm_test",
        trace_id="trace_adapter",
        request_id="req_adapter",
        caller_component="AgentRunner",
        model_alias="proxy-model",
        messages=[LlmMessageDto(role=LlmMessageRole.USER, content="hello")],
        response_format=LlmResponseFormatDto(),
        tool_schemas=[],
        stream=stream,
        generation_params={"temperature": 0.1},
    )


def _route() -> LlmProviderRouteConfig:
    """构建测试用供应商路由配置。

    :return: LlmProviderRouteConfig。
    """

    return LlmProviderRouteConfig(
        provider_route_id="route_adapter",
        provider_name="proxy",
        base_url="http://proxy.test",
        model_alias="proxy-model",
    )


def _timeout_policy() -> LlmTimeoutPolicyConfig:
    """构建测试用短超时策略。

    :return: LlmTimeoutPolicyConfig。
    """

    return LlmTimeoutPolicyConfig(
        connect_timeout_seconds=0.1,
        first_token_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        total_timeout_seconds=2.0,
    )


def test_openai_adapter_posts_chat_completion_payload() -> None:
    """验证适配器将归一化请求转换为 OpenAI-compatible 请求体。

    :return: None。
    """

    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """处理 MockTransport 请求并记录请求体。

        :param request: httpx 发出的请求对象。
        :return: OpenAI-compatible 成功响应。
        """

        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "actual-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        route=_route(),
        timeout_policy=_timeout_policy(),
        client=client,
    )

    response = asyncio.run(adapter.invoke(_provider_request()))

    assert response.actual_model == "actual-model"
    assert response.content == "ok"
    assert captured_payloads[0]["model"] == "proxy-model"
    assert captured_payloads[0]["temperature"] == 0.1
    asyncio.run(adapter.close())
    asyncio.run(client.aclose())


def test_openai_adapter_maps_rate_limit_response() -> None:
    """验证 429 响应会映射为 LLM_RATE_LIMITED。

    :return: None。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """返回模型代理限流响应。

        :param request: httpx 发出的请求对象。
        :return: 429 JSON 响应。
        """

        del request
        return httpx.Response(
            429,
            json={"error": {"type": "rate_limit", "code": "rate_limited"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        route=_route(),
        timeout_policy=_timeout_policy(),
        client=client,
    )

    with pytest.raises(LlmGatewayError) as exc_info:
        asyncio.run(adapter.invoke(_provider_request()))

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_RATE_LIMITED
    asyncio.run(adapter.close())
    asyncio.run(client.aclose())


def test_openai_adapter_parses_sse_events() -> None:
    """验证适配器可解析 OpenAI-compatible SSE chunk。

    :return: None。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """返回测试用 SSE 响应。

        :param request: httpx 发出的请求对象。
        :return: 包含 delta、usage、done 的 SSE 响应。
        """

        del request
        content = "\n\n".join(
            [
                'data: {"model":"actual-model","choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
                'data: {"model":"actual-model","choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}',
                'data: {"model":"actual-model","choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ]
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content.encode("utf-8"),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        route=_route(),
        timeout_policy=_timeout_policy(),
        client=client,
    )

    async def collect() -> list[ProviderStreamEventDto]:
        """收集适配器流式事件。

        :return: ProviderStreamEventDto 列表。
        """

        return [event async for event in adapter.stream(_provider_request(stream=True))]

    events = asyncio.run(collect())

    assert [event.event_type for event in events] == [
        ProviderStreamEventType.DELTA,
        ProviderStreamEventType.USAGE,
        ProviderStreamEventType.COMPLETED,
    ]
    assert events[0].delta == "你"
    asyncio.run(adapter.close())
    asyncio.run(client.aclose())
