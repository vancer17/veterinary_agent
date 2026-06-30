#########################################################################
# 模块：tests.test_l0_middlewares
# 用途：验证 L0 HTTP 入口三件套中间件的核心契约。
# 层级：测试层；基于 pytest 的 ASGI 原生单元测试。
# 契约：仅通过 veterinary_agent.http 公开入口导入被测对象。
#########################################################################

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import MutableMapping, Sequence
from typing import Any, Final, cast
from uuid import UUID

import pytest

from veterinary_agent.http import (
    AsgiApp,
    HeaderPair,
    Message,
    Receive,
    RequestLogMiddleware,
    RequestLogMiddlewareSettings,
    Scope,
    Send,
    ServiceKeyMiddleware,
    ServiceKeyMiddlewareSettings,
    TraceIdMiddleware,
    TraceIdMiddlewareSettings,
    get_current_trace_id,
)

type TraceObservation = tuple[str | None, str | None]

_DEFAULT_SCOPE_CLIENT: Final[tuple[str, int]] = ("127.0.0.1", 52190)
_HTTP_200_OK: Final[int] = 200
_HTTP_201_CREATED: Final[int] = 201
_HTTP_202_ACCEPTED: Final[int] = 202
_HTTP_204_NO_CONTENT: Final[int] = 204
_HTTP_403_FORBIDDEN: Final[int] = 403
_UUIDV7_VERSION: Final[int] = 7


class ExpectedAppError(RuntimeError):
    """用于验证异常日志路径的测试异常。"""


def _build_scope(
    *,
    path: str = "/v1/chat/completions",
    method: str = "POST",
    headers: Sequence[HeaderPair] | None = None,
    state: MutableMapping[str, Any] | None = None,
    client: tuple[str, int] | None = _DEFAULT_SCOPE_CLIENT,
) -> Scope:
    """构造测试用 ASGI scope。

    :param path: 请求路径。
    :type path: str
    :param method: HTTP 方法。
    :type method: str
    :param headers: 请求头序列。
    :type headers: Sequence[HeaderPair] | None
    :param state: 可选 ASGI state 映射。
    :type state: MutableMapping[str, Any] | None
    :param client: 可选客户端地址。
    :type client: tuple[str, int] | None
    :return: 测试用 ASGI scope。
    :rtype: Scope
    """

    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "headers": tuple(headers or ()),
    }
    if state is not None:
        scope["state"] = state
    if client is not None:
        scope["client"] = client
    return scope


def _ok_app(
    *,
    status_code: int = 200,
    body: bytes = b"ok",
    headers: Sequence[HeaderPair] | None = None,
) -> AsgiApp:
    """创建固定成功响应的 ASGI 应用。

    :param status_code: 响应状态码。
    :type status_code: int
    :param body: 响应 body。
    :type body: bytes
    :param headers: 响应头序列。
    :type headers: Sequence[HeaderPair] | None
    :return: ASGI 测试应用。
    :rtype: AsgiApp
    """

    response_headers = list(headers or ())

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """发送固定成功响应。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param receive: ASGI receive 回调。
        :type receive: Receive
        :param send: ASGI send 回调。
        :type send: Send
        :return: 无返回值。
        :rtype: None
        """

        del scope, receive

        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _counting_app(calls: list[Scope]) -> AsgiApp:
    """创建会记录调用次数的 ASGI 应用。

    :param calls: 保存已调用 scope 的列表。
    :type calls: list[Scope]
    :return: ASGI 测试应用。
    :rtype: AsgiApp
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """记录调用并返回成功响应。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param receive: ASGI receive 回调。
        :type receive: Receive
        :param send: ASGI send 回调。
        :type send: Send
        :return: 无返回值。
        :rtype: None
        """

        calls.append(scope)
        await _ok_app()(scope, receive, send)

    return app


def _trace_recording_app(
    observations: list[TraceObservation],
    *,
    response_headers: Sequence[HeaderPair] | None = None,
) -> AsgiApp:
    """创建记录 state 与上下文 trace_id 的 ASGI 应用。

    :param observations: 保存 trace_id 观察结果的列表。
    :type observations: list[TraceObservation]
    :param response_headers: 响应头序列。
    :type response_headers: Sequence[HeaderPair] | None
    :return: ASGI 测试应用。
    :rtype: AsgiApp
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """记录 trace_id 并返回成功响应。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param receive: ASGI receive 回调。
        :type receive: Receive
        :param send: ASGI send 回调。
        :type send: Send
        :return: 无返回值。
        :rtype: None
        """

        state = scope.get("state")
        state_trace_id: str | None = None
        if isinstance(state, MutableMapping):
            value = state.get("trace_id")
            if isinstance(value, str):
                state_trace_id = value

        observations.append((state_trace_id, get_current_trace_id()))
        await _ok_app(headers=response_headers)(scope, receive, send)

    return app


def _raising_app() -> AsgiApp:
    """创建会抛出测试异常的 ASGI 应用。

    :return: ASGI 测试应用。
    :rtype: AsgiApp
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        """抛出测试异常。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param receive: ASGI receive 回调。
        :type receive: Receive
        :param send: ASGI send 回调。
        :type send: Send
        :return: 无返回值。
        :rtype: None
        :raises ExpectedAppError: 始终抛出测试异常。
        """

        del scope, receive, send

        raise ExpectedAppError("测试异常")

    return app


async def _run_asgi(app: AsgiApp, scope: Scope) -> list[Message]:
    """执行一次 ASGI 调用并收集响应消息。

    :param app: ASGI 应用。
    :type app: AsgiApp
    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :return: 响应消息列表。
    :rtype: list[Message]
    """

    messages: list[Message] = []

    async def receive() -> Message:
        """返回空 HTTP 请求消息。

        :return: ASGI 请求消息。
        :rtype: Message
        """

        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        """收集 ASGI 响应消息。

        :param message: ASGI 响应消息。
        :type message: Message
        :return: 无返回值。
        :rtype: None
        """

        messages.append(message)

    await app(scope, receive, send)
    return messages


def _run(app: AsgiApp, scope: Scope) -> list[Message]:
    """同步执行一次 ASGI 调用。

    :param app: ASGI 应用。
    :type app: AsgiApp
    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :return: 响应消息列表。
    :rtype: list[Message]
    """

    return asyncio.run(_run_asgi(app, scope))


def _response_start(messages: Sequence[Message]) -> Message:
    """读取响应开始消息。

    :param messages: ASGI 响应消息序列。
    :type messages: Sequence[Message]
    :return: 响应开始消息。
    :rtype: Message
    :raises AssertionError: 当响应开始消息不存在时抛出。
    """

    for message in messages:
        if message.get("type") == "http.response.start":
            return message
    raise AssertionError("响应开始消息不存在。")


def _response_status(messages: Sequence[Message]) -> int:
    """读取响应状态码。

    :param messages: ASGI 响应消息序列。
    :type messages: Sequence[Message]
    :return: HTTP 响应状态码。
    :rtype: int
    """

    status = _response_start(messages).get("status")
    assert isinstance(status, int)
    return status


def _response_headers(messages: Sequence[Message]) -> dict[bytes, bytes]:
    """读取响应头。

    :param messages: ASGI 响应消息序列。
    :type messages: Sequence[Message]
    :return: 小写响应头映射。
    :rtype: dict[bytes, bytes]
    """

    headers = cast(Sequence[HeaderPair], _response_start(messages).get("headers", ()))
    return {name.lower(): value for name, value in headers}


def _response_body(messages: Sequence[Message]) -> bytes:
    """拼接响应 body。

    :param messages: ASGI 响应消息序列。
    :type messages: Sequence[Message]
    :return: 响应 body。
    :rtype: bytes
    """

    chunks: list[bytes] = []
    for message in messages:
        if message.get("type") != "http.response.body":
            continue
        body = message.get("body", b"")
        assert isinstance(body, bytes)
        chunks.append(body)
    return b"".join(chunks)


def _json_response(messages: Sequence[Message]) -> dict[str, Any]:
    """解析 JSON 响应 body。

    :param messages: ASGI 响应消息序列。
    :type messages: Sequence[Message]
    :return: JSON 对象。
    :rtype: dict[str, Any]
    """

    payload = json.loads(_response_body(messages).decode("utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _single_log_payload(
    caplog: pytest.LogCaptureFixture,
    *,
    logger_name: str,
) -> dict[str, Any]:
    """读取单条结构化访问日志载荷。

    :param caplog: pytest 日志捕获夹具。
    :type caplog: pytest.LogCaptureFixture
    :param logger_name: 目标日志器名称。
    :type logger_name: str
    :return: 访问日志 JSON 载荷。
    :rtype: dict[str, Any]
    """

    records = [record for record in caplog.records if record.name == logger_name]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


@pytest.mark.parametrize(
    "headers",
    [
        (),
        ((b"x-service-key", b"wrong-secret"),),
    ],
)
def test_service_key_middleware_rejects_missing_or_wrong_key(
    headers: tuple[HeaderPair, ...],
) -> None:
    """校验缺失或错误服务间密钥时返回契约化 403 响应。

    :param headers: 请求头序列。
    :type headers: tuple[HeaderPair, ...]
    :return: 无返回值。
    :rtype: None
    """

    calls: list[Scope] = []
    app = ServiceKeyMiddleware(
        _counting_app(calls),
        settings=ServiceKeyMiddlewareSettings(service_key="expected-secret"),
    )

    messages = _run(app, _build_scope(headers=headers))

    assert calls == []
    assert _response_status(messages) == _HTTP_403_FORBIDDEN
    payload = _json_response(messages)
    assert payload["error"]["code"] == "invalid_service_key"
    assert payload["error"]["type"] == "authentication_error"


def test_service_key_middleware_allows_matching_key() -> None:
    """校验正确服务间密钥会放行请求。

    :return: 无返回值。
    :rtype: None
    """

    calls: list[Scope] = []
    app = ServiceKeyMiddleware(
        _counting_app(calls),
        settings=ServiceKeyMiddlewareSettings(service_key="expected-secret"),
    )

    messages = _run(
        app,
        _build_scope(headers=((b"x-service-key", b"expected-secret"),)),
    )

    assert len(calls) == 1
    assert _response_status(messages) == _HTTP_200_OK
    assert _response_body(messages) == b"ok"


def test_service_key_middleware_skips_public_paths_and_disabled_key() -> None:
    """校验公开路径和空密钥配置不会触发服务间密钥校验。

    :return: 无返回值。
    :rtype: None
    """

    protected_app = ServiceKeyMiddleware(
        _ok_app(status_code=204),
        settings=ServiceKeyMiddlewareSettings(service_key="expected-secret"),
    )
    disabled_app = ServiceKeyMiddleware(
        _ok_app(status_code=202),
        settings=ServiceKeyMiddlewareSettings(service_key=""),
    )

    health_messages = _run(protected_app, _build_scope(path="/health", method="GET"))
    disabled_messages = _run(disabled_app, _build_scope())

    assert _response_status(health_messages) == _HTTP_204_NO_CONTENT
    assert _response_status(disabled_messages) == _HTTP_202_ACCEPTED


def test_service_key_middleware_can_protect_ready_endpoint() -> None:
    """校验配置开启后 ``/ready`` 也需要服务间密钥。

    :return: 无返回值。
    :rtype: None
    """

    app = ServiceKeyMiddleware(
        _ok_app(),
        settings=ServiceKeyMiddlewareSettings(
            service_key="expected-secret",
            ready_requires_service_key=True,
        ),
    )

    messages = _run(app, _build_scope(path="/ready", method="GET"))

    assert _response_status(messages) == _HTTP_403_FORBIDDEN
    assert _json_response(messages)["error"]["code"] == "invalid_service_key"


def test_trace_id_middleware_prefers_request_header_and_cleans_context() -> None:
    """校验请求头 trace_id 优先、写入 state、回写响应头并清理上下文。

    :return: 无返回值。
    :rtype: None
    """

    observations: list[TraceObservation] = []
    app = TraceIdMiddleware(
        _trace_recording_app(
            observations,
            response_headers=((b"x-trace-id", b"old-trace"),),
        ),
        settings=TraceIdMiddlewareSettings(header_name="X-Trace-Id"),
    )
    scope = _build_scope(headers=((b"x-trace-id", b"inbound-trace"),))

    messages = _run(app, scope)

    assert observations == [("inbound-trace", "inbound-trace")]
    assert _response_headers(messages)[b"x-trace-id"] == b"inbound-trace"
    assert get_current_trace_id() is None


def test_trace_id_middleware_generates_uuidv7_when_header_missing() -> None:
    """校验缺失请求头时生成 UUIDv7 trace_id。

    :return: 无返回值。
    :rtype: None
    """

    app = TraceIdMiddleware(_ok_app())
    scope = _build_scope(headers=())

    messages = _run(app, scope)

    state = scope.get("state")
    assert isinstance(state, MutableMapping)
    trace_id = state.get("trace_id")
    assert isinstance(trace_id, str)
    assert UUID(hex=trace_id).version == _UUIDV7_VERSION
    assert _response_headers(messages)[b"x-trace-id"] == trace_id.encode("latin-1")


def test_trace_id_middleware_can_disable_response_header() -> None:
    """校验可关闭 trace_id 响应头回写。

    :return: 无返回值。
    :rtype: None
    """

    def fixed_trace_id_factory() -> str:
        """返回固定 trace_id。

        :return: 固定 trace_id。
        :rtype: str
        """

        return "fixed-trace-id"

    app = TraceIdMiddleware(
        _ok_app(),
        settings=TraceIdMiddlewareSettings(response_header_enabled=False),
        trace_id_factory=fixed_trace_id_factory,
    )
    scope = _build_scope(headers=())

    messages = _run(app, scope)

    state = scope.get("state")
    assert isinstance(state, MutableMapping)
    assert state["trace_id"] == "fixed-trace-id"
    assert b"x-trace-id" not in _response_headers(messages)


def test_request_log_middleware_emits_structured_log_without_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """校验访问日志输出结构化元信息且不记录请求 body。

    :param caplog: pytest 日志捕获夹具。
    :type caplog: pytest.LogCaptureFixture
    :return: 无返回值。
    :rtype: None
    """

    logger_name = "tests.access.ok"
    caplog.set_level(logging.INFO, logger=logger_name)
    app = RequestLogMiddleware(
        _ok_app(status_code=201),
        settings=RequestLogMiddlewareSettings(
            logger_name=logger_name,
            include_client_host=True,
            include_user_agent=True,
        ),
    )
    scope = _build_scope(
        headers=(
            (b"user-agent", b"pytest-agent"),
            (b"authorization", b"secret-token"),
        ),
        state={"trace_id": "trace-123"},
        client=("10.0.0.8", 43000),
    )

    messages = _run(app, scope)

    payload = _single_log_payload(caplog, logger_name=logger_name)
    assert _response_status(messages) == _HTTP_201_CREATED
    assert payload["trace_id"] == "trace-123"
    assert payload["method"] == "POST"
    assert payload["path"] == "/v1/chat/completions"
    assert payload["status_code"] == _HTTP_201_CREATED
    assert payload["outcome"] == "ok"
    assert payload["client_host"] == "10.0.0.8"
    assert payload["user_agent"] == "pytest-agent"
    assert isinstance(payload["duration_ms"], float)
    assert payload["duration_ms"] >= 0.0
    assert "body" not in payload
    assert "messages" not in payload
    assert "authorization" not in payload


def test_request_log_middleware_logs_and_reraises_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """校验异常路径会记录错误日志并继续抛出原异常。

    :param caplog: pytest 日志捕获夹具。
    :type caplog: pytest.LogCaptureFixture
    :return: 无返回值。
    :rtype: None
    """

    logger_name = "tests.access.error"
    caplog.set_level(logging.INFO, logger=logger_name)
    app = RequestLogMiddleware(
        _raising_app(),
        settings=RequestLogMiddlewareSettings(logger_name=logger_name),
    )
    scope = _build_scope(state={"trace_id": "trace-error"})

    with pytest.raises(ExpectedAppError):
        _run(app, scope)

    payload = _single_log_payload(caplog, logger_name=logger_name)
    assert payload["trace_id"] == "trace-error"
    assert payload["status_code"] is None
    assert payload["outcome"] == "error"
    assert payload["exception_type"] == "ExpectedAppError"


def test_l0_middlewares_work_together_in_declared_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """校验三件套按声明顺序组合后共享 trace_id 并输出访问日志。

    :param caplog: pytest 日志捕获夹具。
    :type caplog: pytest.LogCaptureFixture
    :return: 无返回值。
    :rtype: None
    """

    logger_name = "tests.access.chain"
    caplog.set_level(logging.INFO, logger=logger_name)
    app = ServiceKeyMiddleware(
        TraceIdMiddleware(
            RequestLogMiddleware(
                _ok_app(status_code=202),
                settings=RequestLogMiddlewareSettings(logger_name=logger_name),
            )
        ),
        settings=ServiceKeyMiddlewareSettings(service_key="expected-secret"),
    )
    scope = _build_scope(headers=((b"x-service-key", b"expected-secret"),))

    messages = _run(app, scope)

    state = scope.get("state")
    assert isinstance(state, MutableMapping)
    trace_id = state.get("trace_id")
    assert isinstance(trace_id, str)
    assert UUID(hex=trace_id).version == _UUIDV7_VERSION
    assert _response_status(messages) == _HTTP_202_ACCEPTED
    assert _response_headers(messages)[b"x-trace-id"] == trace_id.encode("latin-1")

    payload = _single_log_payload(caplog, logger_name=logger_name)
    assert payload["trace_id"] == trace_id
    assert payload["status_code"] == _HTTP_202_ACCEPTED
    assert payload["outcome"] == "ok"
