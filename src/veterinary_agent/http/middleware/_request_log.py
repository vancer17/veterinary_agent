#########################################################################
# 模块：veterinary_agent.http.middleware._request_log
# 用途：实现 L0 HTTP 入口的访问日志中间件。
# 层级：L0 HTTP 适配层；ASGI 原生中间件。
# 契约：仅记录请求元信息，不记录 body；输出结构化日志。
# 备注：本模块为私有实现。请从 veterinary_agent.http.middleware 或
#        veterinary_agent.http 导入公开符号。
#########################################################################

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from time import perf_counter_ns
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict

type LogLevel = int
type HeaderName = bytes
type Scope = MutableMapping[str, Any]
type HeaderPair = tuple[bytes, bytes]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]
type RequestOutcome = Literal["ok", "error"]

_DEFAULT_ACCESS_LOGGER_NAME: Final[str] = "veterinary_agent.http.access"
_DEFAULT_TRACE_ID_STATE_KEY: Final[str] = "trace_id"
_DEFAULT_USER_AGENT_HEADER: Final[str] = "user-agent"


class RequestLogMiddlewareSettings(BaseModel):
    """访问日志中间件配置。

    :param logger_name: 默认日志器名称。
    :type logger_name: str
    :param level: 默认日志级别。
    :type level: int
    :param state_key: 从 ASGI ``scope["state"]`` 读取 trace_id 的键名。
    :type state_key: str
    :param include_client_host: 是否记录客户端主机地址。
    :type include_client_host: bool
    :param include_user_agent: 是否记录 ``User-Agent``。
    :type include_user_agent: bool
    """

    model_config = ConfigDict(frozen=True)

    logger_name: str = _DEFAULT_ACCESS_LOGGER_NAME
    level: LogLevel = logging.INFO
    state_key: str = _DEFAULT_TRACE_ID_STATE_KEY
    include_client_host: bool = True
    include_user_agent: bool = False


class RequestLogRecord(BaseModel):
    """一次请求的访问日志记录。

    :param trace_id: 当前请求的 trace_id。
    :type trace_id: str | None
    :param method: HTTP 方法。
    :type method: str
    :param path: HTTP 请求路径。
    :type path: str
    :param status_code: HTTP 响应状态码。
    :type status_code: int | None
    :param duration_ms: 请求耗时，单位为毫秒。
    :type duration_ms: float
    :param outcome: 请求结果。
    :type outcome: RequestOutcome
    :param client_host: 客户端主机地址。
    :type client_host: str | None
    :param user_agent: 请求 ``User-Agent``。
    :type user_agent: str | None
    :param exception_type: 异常类型名。
    :type exception_type: str | None
    """

    model_config = ConfigDict(frozen=True)

    trace_id: str | None
    method: str
    path: str
    status_code: int | None
    duration_ms: float
    outcome: RequestOutcome
    client_host: str | None = None
    user_agent: str | None = None
    exception_type: str | None = None


def _normalize_header_name(header_name: str) -> HeaderName:
    """规范化请求头名称。

    :param header_name: 原始请求头名称。
    :type header_name: str
    :return: 小写后的 ASCII 字节串请求头名称。
    :rtype: bytes
    """

    return header_name.strip().lower().encode("ascii")


def _get_header_value(scope: Scope, header_name: HeaderName) -> str | None:
    """从 ASGI scope 中读取指定请求头。

    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :param header_name: 已规范化的小写请求头名称。
    :type header_name: bytes
    :return: 请求头值；不存在或为空白时返回 ``None``。
    :rtype: str | None
    """

    headers = cast(tuple[HeaderPair, ...], scope.get("headers", ()))
    for name, value in headers:
        if name.lower() == header_name:
            decoded = value.decode("latin-1").strip()
            return decoded or None
    return None


def _get_trace_id_from_scope(
    scope: Scope,
    *,
    state_key: str,
) -> str | None:
    """从请求 scope 中解析 trace_id。

    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :param state_key: ``scope["state"]`` 中的 trace_id 键名。
    :type state_key: str
    :return: 当前请求的 trace_id；不存在时返回 ``None``。
    :rtype: str | None
    """

    state = scope.get("state")
    if isinstance(state, MutableMapping):
        value = state.get(state_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_client_host(scope: Scope) -> str | None:
    """从 ASGI scope 中读取客户端主机地址。

    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :return: 客户端主机地址；不存在时返回 ``None``。
    :rtype: str | None
    """

    client = scope.get("client")
    if not isinstance(client, tuple) or len(client) < 1:
        return None

    host = client[0]
    if isinstance(host, str) and host.strip():
        return host.strip()
    return None


def _build_request_log_payload(record: RequestLogRecord) -> dict[str, Any]:
    """把访问日志记录转换为结构化载荷。

    :param record: 请求日志记录。
    :type record: RequestLogRecord
    :return: 可序列化的结构化日志载荷。
    :rtype: dict[str, Any]
    """

    payload: dict[str, Any] = {
        "trace_id": record.trace_id,
        "method": record.method,
        "path": record.path,
        "status_code": record.status_code,
        "duration_ms": record.duration_ms,
        "outcome": record.outcome,
    }
    if record.client_host is not None:
        payload["client_host"] = record.client_host
    if record.user_agent is not None:
        payload["user_agent"] = record.user_agent
    if record.exception_type is not None:
        payload["exception_type"] = record.exception_type
    return payload


def _serialize_request_log_payload(payload: dict[str, Any]) -> str:
    """把结构化日志载荷序列化为一行 JSON。

    :param payload: 结构化日志载荷。
    :type payload: dict[str, Any]
    :return: JSON 行文本。
    :rtype: str
    """

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RequestLogMiddleware:
    """ASGI 访问日志中间件。"""

    def __init__(
        self,
        app: AsgiApp,
        *,
        logger: logging.Logger | None = None,
        settings: RequestLogMiddlewareSettings | None = None,
    ) -> None:
        """初始化访问日志中间件。

        :param app: 下游 ASGI 应用。
        :type app: AsgiApp
        :param logger: 可选日志器；未传入时按 ``settings.logger_name`` 获取。
        :type logger: logging.Logger | None
        :param settings: 访问日志配置。
        :type settings: RequestLogMiddlewareSettings | None
        :return: 无返回值。
        :rtype: None
        """

        self._app = app
        self._settings = settings or RequestLogMiddlewareSettings()
        self._logger = logger or logging.getLogger(self._settings.logger_name)
        self._user_agent_header = _normalize_header_name(_DEFAULT_USER_AGENT_HEADER)
        self._state_key = self._settings.state_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """处理一次 ASGI 请求。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param receive: ASGI receive 回调。
        :type receive: Receive
        :param send: ASGI send 回调。
        :type send: Send
        :return: 无返回值。
        :rtype: None
        """

        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        started_ns = perf_counter_ns()
        response_state: dict[str, int | None] = {"status_code": None}
        exception_type: str | None = None

        try:
            await self._app(scope, receive, self._wrap_send(send, response_state))
        except Exception as exc:
            exception_type = exc.__class__.__name__
            raise
        finally:
            self._emit_request_log(
                scope,
                started_ns=started_ns,
                response_state=response_state,
                exception_type=exception_type,
            )

    def _wrap_send(
        self,
        send: Send,
        response_state: dict[str, int | None],
    ) -> Send:
        """创建能够捕获响应状态码的 send 闭包。

        :param send: 原始 ASGI send 回调。
        :type send: Send
        :param response_state: 保存响应状态码的可变容器。
        :type response_state: dict[str, int | None]
        :return: 包装后的 ASGI send 回调。
        :rtype: Send
        """

        async def send_with_status(message: Message) -> None:
            """捕获响应状态码并继续发送 ASGI 消息。

            :param message: ASGI 消息。
            :type message: Message
            :return: 无返回值。
            :rtype: None
            """

            if (
                message.get("type") == "http.response.start"
                and response_state["status_code"] is None
            ):
                status = message.get("status")
                if isinstance(status, int):
                    response_state["status_code"] = status

            await send(message)

        return send_with_status

    def _emit_request_log(
        self,
        scope: Scope,
        *,
        started_ns: int,
        response_state: dict[str, int | None],
        exception_type: str | None,
    ) -> None:
        """输出一次访问日志。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :param started_ns: 请求开始时间戳。
        :type started_ns: int
        :param response_state: 保存响应状态码的可变容器。
        :type response_state: dict[str, int | None]
        :param exception_type: 异常类型名；无异常时为 ``None``。
        :type exception_type: str | None
        :return: 无返回值。
        :rtype: None
        """

        duration_ms = round((perf_counter_ns() - started_ns) / 1_000_000, 3)
        status_code = response_state["status_code"]
        outcome: RequestOutcome = "error" if exception_type is not None else "ok"

        record = RequestLogRecord(
            trace_id=_get_trace_id_from_scope(scope, state_key=self._state_key),
            method=str(scope.get("method") or ""),
            path=str(scope.get("path") or ""),
            status_code=status_code,
            duration_ms=duration_ms,
            outcome=outcome,
            client_host=(
                _get_client_host(scope) if self._settings.include_client_host else None
            ),
            user_agent=(
                _get_header_value(scope, self._user_agent_header)
                if self._settings.include_user_agent
                else None
            ),
            exception_type=exception_type,
        )
        payload = _build_request_log_payload(record)
        message = _serialize_request_log_payload(payload)

        if self._logger.isEnabledFor(self._settings.level):
            self._logger.log(self._settings.level, message)


__all__: Final[tuple[str, ...]] = (
    "RequestLogMiddleware",
    "RequestLogMiddlewareSettings",
    "RequestLogRecord",
)
