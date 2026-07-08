##################################################################################################
# 文件: tests/api_ingress/test_rate_limit.py
# 作用: 验证 API 接入组件实例级限流器会消费 rate_limit.* 配置并映射为统一限流响应。
# 边界: 仅测试 ApiIngress 本地内存级入口治理；不接入网关、Redis、真实 SSE Mapper 或领域业务组件。
##################################################################################################

import asyncio
from typing import cast

from fastapi import Request
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from veterinary_agent import (
    ApiIngressRateLimiter,
    ApiIngressSettings,
    ErrorDetailDto,
    ResponseMode,
    create_app,
)


class _ManualClock:
    """用于限流滑动窗口测试的手动单调时钟。"""

    def __init__(self, initial_value: float = 0.0) -> None:
        """初始化手动时钟。

        :param initial_value: 初始单调时间值。
        :return: 无返回值。
        """

        self._value = initial_value

    def now(self) -> float:
        """读取当前手动时钟值。

        :return: 当前单调时间值。
        """

        return self._value

    def advance(self, seconds: float) -> None:
        """推进手动时钟。

        :param seconds: 需要推进的秒数。
        :return: 无返回值。
        """

        self._value += seconds


def _settings_with_rate_limit(**rate_limit_updates: object) -> ApiIngressSettings:
    """构建带有 rate_limit 配置覆盖项的 API 接入组件配置。

    :param rate_limit_updates: rate_limit 配置字段覆盖值。
    :return: 已合并 rate_limit 覆盖项的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "rate_limit": base_settings.rate_limit.model_copy(
                update={"enabled": True, **rate_limit_updates}
            ),
        }
    )


def _valid_payload(request_id: str = "req_rate_limit_001") -> dict[str, object]:
    """构建可通过入口校验并抵达编排 TODO 占位的最小请求。

    :param request_id: 请求体中的 request_id。
    :return: 最小合法一轮对话请求体。
    """

    return {
        "request_id": request_id,
        "trace_id": f"trace_{request_id}",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "小狗今天精神一般，需要先观察哪些症状？",
                    }
                ],
            }
        ],
        "vet_context": {
            "user_id": "user_001",
            "session_id": "session_001",
            "pet_id": "pet_001",
        },
    }


def _build_request(path: str, client_host: str) -> Request:
    """构建用于限流器测试的最小 HTTP 请求对象。

    :param path: 请求路径。
    :param client_host: 客户端来源主机。
    :return: FastAPI 请求对象。
    """

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": (client_host, 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        """模拟 ASGI 请求体接收事件。

        :return: ASGI HTTP request 消息。
        """

        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive=receive)


def _response_body(response_json: object) -> dict[str, object]:
    """将响应 JSON 约束为字典。

    :param response_json: HTTP 响应解析后的 JSON 对象。
    :return: 字典形式的响应体。
    """

    assert isinstance(response_json, dict)
    return cast(dict[str, object], response_json)


def _detail_fields(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细字段集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 field 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    fields: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        field = detail.get("field")
        if isinstance(field, str):
            fields.add(field)
    return fields


def _detail_reasons(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细原因集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 reason 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    reasons: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
    return reasons


def _error_detail_fields(details: list[ErrorDetailDto]) -> set[str]:
    """提取限流判定明细中的字段集合。

    :param details: 限流器返回的错误明细列表。
    :return: details 数组中的非空 field 字段集合。
    """

    fields: set[str] = set()
    for detail in details:
        if detail.field is not None:
            fields.add(detail.field)
    return fields


async def _exercise_disabled_rate_limiter() -> None:
    """验证关闭限流时不记录请求窗口。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    limiter = ApiIngressRateLimiter.from_settings(settings)
    request = _build_request("/agent/turns", "client-a")

    first_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.SYNC,
    )
    second_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.SYNC,
    )

    assert first_decision.allowed
    assert second_decision.allowed
    assert await limiter.request_count(request) == 0


async def _exercise_per_client_source_window() -> None:
    """验证按客户端来源拆分请求速率窗口。

    :return: 无返回值。
    """

    settings = _settings_with_rate_limit(
        max_requests_per_minute=1,
        per_client_source_enabled=True,
    )
    limiter = ApiIngressRateLimiter.from_settings(settings)
    client_a_request = _build_request("/agent/turns", "client-a")
    client_b_request = _build_request("/agent/turns", "client-b")

    first_decision = await limiter.try_acquire(
        request=client_a_request,
        response_mode=ResponseMode.SYNC,
    )
    second_decision = await limiter.try_acquire(
        request=client_a_request,
        response_mode=ResponseMode.SYNC,
    )
    third_decision = await limiter.try_acquire(
        request=client_b_request,
        response_mode=ResponseMode.SYNC,
    )

    assert first_decision.allowed
    assert not second_decision.allowed
    assert third_decision.allowed
    assert _error_detail_fields(second_decision.details) >= {
        "rate_limit.max_requests_per_minute"
    }


async def _exercise_merged_path_window() -> None:
    """验证关闭按路径拆分后不同路径共享请求速率窗口。

    :return: 无返回值。
    """

    settings = _settings_with_rate_limit(
        max_requests_per_minute=1,
        per_path_enabled=False,
    )
    limiter = ApiIngressRateLimiter.from_settings(settings)

    first_decision = await limiter.try_acquire(
        request=_build_request("/agent/turns", "client-a"),
        response_mode=ResponseMode.SYNC,
    )
    second_decision = await limiter.try_acquire(
        request=_build_request("/openai/v1/responses", "client-a"),
        response_mode=ResponseMode.SYNC,
    )

    assert first_decision.allowed
    assert not second_decision.allowed
    assert _error_detail_fields(second_decision.details) >= {
        "rate_limit.max_requests_per_minute"
    }


async def _exercise_active_stream_limit() -> None:
    """验证活跃 SSE 连接数量达到上限后拒绝新流式许可。

    :return: 无返回值。
    """

    settings = _settings_with_rate_limit(max_active_streams=1)
    limiter = ApiIngressRateLimiter.from_settings(settings)
    request = _build_request("/agent/turns", "client-a")

    first_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.STREAM,
    )
    second_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.STREAM,
    )
    assert first_decision.allowed
    assert first_decision.stream_lease is not None
    assert not second_decision.allowed
    assert _error_detail_fields(second_decision.details) >= {
        "rate_limit.max_active_streams"
    }
    assert await limiter.active_stream_count() == 1

    await first_decision.stream_lease.release()
    assert await limiter.active_stream_count() == 0


async def _exercise_sliding_window_expiration() -> None:
    """验证请求速率窗口会在 60 秒后过期。

    :return: 无返回值。
    """

    clock = _ManualClock()
    settings = _settings_with_rate_limit(max_requests_per_minute=1)
    limiter = ApiIngressRateLimiter.from_settings(settings, time_provider=clock.now)
    request = _build_request("/agent/turns", "client-a")

    first_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.SYNC,
    )
    second_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.SYNC,
    )
    clock.advance(60.0)
    third_decision = await limiter.try_acquire(
        request=request,
        response_mode=ResponseMode.SYNC,
    )

    assert first_decision.allowed
    assert not second_decision.allowed
    assert second_decision.retry_after_seconds == 60
    assert third_decision.allowed


def test_rate_limiter_noops_when_disabled() -> None:
    """验证 rate_limit.enabled 关闭时限流器不记录请求。

    :return: 无返回值。
    """

    asyncio.run(_exercise_disabled_rate_limiter())


def test_rate_limiter_separates_windows_by_client_source() -> None:
    """验证 rate_limit.per_client_source_enabled 会按客户端来源拆分窗口。

    :return: 无返回值。
    """

    asyncio.run(_exercise_per_client_source_window())


def test_rate_limiter_can_merge_windows_across_paths() -> None:
    """验证 rate_limit.per_path_enabled 关闭时不同路径共享窗口。

    :return: 无返回值。
    """

    asyncio.run(_exercise_merged_path_window())


def test_rate_limiter_rejects_when_active_stream_capacity_is_exhausted() -> None:
    """验证 rate_limit.max_active_streams 会限制活跃流式许可数量。

    :return: 无返回值。
    """

    asyncio.run(_exercise_active_stream_limit())


def test_rate_limiter_expires_request_window_after_sixty_seconds() -> None:
    """验证 rate_limit.max_requests_per_minute 使用 60 秒滑动窗口。

    :return: 无返回值。
    """

    asyncio.run(_exercise_sliding_window_expiration())


def test_router_returns_rate_limited_response_when_request_window_is_full() -> None:
    """验证业务入口命中请求速率限制时返回统一 429 响应。

    :return: 无返回值。
    """

    settings = _settings_with_rate_limit(max_requests_per_minute=1)

    with TestClient(create_app(settings)) as client:
        first_response = client.post(
            "/agent/turns",
            json=_valid_payload("req_rate_limit_first"),
        )
        second_response = client.post(
            "/agent/turns",
            json=_valid_payload("req_rate_limit_second"),
        )
    body = _response_body(second_response.json())

    assert first_response.status_code == 503
    assert second_response.status_code == 429
    assert second_response.headers["retry-after"] == "60"
    assert body["code"] == "RATE_LIMITED"
    assert body["request_id"] == "req_rate_limit_second"
    assert body["trace_id"] == "trace_req_rate_limit_second"
    assert "rate_limit.max_requests_per_minute" in _detail_fields(body)
    assert "exceeded" in _detail_reasons(body)


def test_router_separates_request_windows_by_path_when_enabled() -> None:
    """验证默认按路径拆分限流窗口时两个业务入口不会互相占用请求额度。

    :return: 无返回值。
    """

    settings = _settings_with_rate_limit(max_requests_per_minute=1)

    with TestClient(create_app(settings)) as client:
        agent_response = client.post(
            "/agent/turns",
            json=_valid_payload("req_rate_limit_agent"),
        )
        openai_response = client.post(
            "/openai/v1/responses",
            json=_valid_payload("req_rate_limit_openai"),
        )

    assert agent_response.status_code == 503
    assert openai_response.status_code == 503
