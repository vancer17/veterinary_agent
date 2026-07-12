##################################################################################################
# 文件: tests/api_ingress/test_request_parser.py
# 作用: 验证 API 接入组件请求解析器会消费 parse_timeout_seconds 等解析阶段配置。
# 边界: 仅测试 body 读取、JSON 解析和 DTO Validation 的入口解析行为，不接入身份解析、编排层或业务组件。
##################################################################################################

import asyncio

from fastapi import Request
from starlette.types import Message, Scope

from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.api_ingress import parse_agent_turn_request


def _settings_with_parse_timeout(parse_timeout_seconds: float) -> ApiIngressSettings:
    """构建带有请求解析超时覆盖项的 API 接入组件配置。

    :param parse_timeout_seconds: 请求体读取与解析允许的最大秒数。
    :return: 已合并解析超时覆盖项的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "request_limits": base_settings.request_limits.model_copy(
                update={"parse_timeout_seconds": parse_timeout_seconds}
            )
        }
    )


def _build_request(
    body: bytes,
    *,
    body_delay_seconds: float = 0.0,
) -> Request:
    """构建用于请求解析器测试的最小 HTTP 请求对象。

    :param body: 需要模拟接收的原始请求体。
    :param body_delay_seconds: 模拟 ASGI receive 返回请求体前的等待秒数。
    :return: FastAPI 请求对象。
    """

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/agent/turns",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-request-id", b"req_parser_timeout"),
            (b"x-trace-id", b"trace_parser_timeout"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        """模拟 ASGI 请求体接收事件。

        :return: ASGI HTTP request 消息。
        """

        if body_delay_seconds > 0:
            await asyncio.sleep(body_delay_seconds)
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive=receive)


def _detail_reasons(response_json: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细原因集合。

    :param response_json: 字典形式的响应体。
    :return: details 数组中的 reason 字段集合。
    """

    details = response_json.get("details")
    assert isinstance(details, list)
    reasons: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
    return reasons


def test_parser_applies_configured_parse_timeout_to_body_read() -> None:
    """验证请求解析器会使用 parse_timeout_seconds 约束请求体读取。

    :return: 无返回值。
    """

    settings = _settings_with_parse_timeout(parse_timeout_seconds=0.001)
    request = _build_request(
        b'{"input":[]}',
        body_delay_seconds=0.05,
    )

    parse_result = asyncio.run(
        parse_agent_turn_request(request=request, settings=settings)
    )

    assert parse_result.turn_request is None
    assert parse_result.failure is not None
    assert parse_result.failure.status_code == 408
    response_body = parse_result.failure.error_response.model_dump(mode="json")
    assert response_body["code"] == "INVALID_REQUEST"
    assert response_body["request_id"] == "req_parser_timeout"
    assert response_body["trace_id"] == "trace_parser_timeout"
    assert "parse_timeout_exceeded" in _detail_reasons(response_body)
