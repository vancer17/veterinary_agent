#########################################################################
# 模块：veterinary_agent.http.middleware._trace_id
# 用途：实现 L0 HTTP 入口的 trace_id 注入与透传中间件。
# 层级：L0 HTTP 适配层；ASGI 原生中间件。
# 契约：优先使用 X-Trace-Id；缺失或为空时生成新 trace_id。
# 备注：本模块为私有实现。请从 veterinary_agent.http.middleware 或
#        veterinary_agent.http 导入公开符号。
#########################################################################

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from secrets import randbits
from time import time_ns
from typing import Any, Final, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict

type Scope = MutableMapping[str, Any]
type HeaderPair = tuple[bytes, bytes]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]
type TraceIdFactory = Callable[[], str]

_DEFAULT_TRACE_ID_HEADER: Final[str] = "x-trace-id"
_DEFAULT_TRACE_ID_STATE_KEY: Final[str] = "trace_id"
_UUIDV7_TIMESTAMP_BITS: Final[int] = 48
_UUIDV7_RAND_A_BITS: Final[int] = 12
_UUIDV7_RAND_B_BITS: Final[int] = 62
_UUIDV7_VERSION: Final[int] = 0x7
_UUID_VARIANT_RFC_9562: Final[int] = 0b10
_TRACE_ID_CONTEXT: Final[ContextVar[str | None]] = ContextVar(
    "veterinary_agent_trace_id",
    default=None,
)


class TraceIdMiddlewareSettings(BaseModel):
    """trace_id 中间件配置。

    :param header_name: 承载 trace_id 的请求头名称。
    :type header_name: str
    :param state_key: 写入 ASGI ``scope["state"]`` 的键名。
    :type state_key: str
    :param response_header_enabled: 是否在响应头回写 trace_id。
    :type response_header_enabled: bool
    """

    model_config = ConfigDict(frozen=True)

    header_name: str = _DEFAULT_TRACE_ID_HEADER
    state_key: str = _DEFAULT_TRACE_ID_STATE_KEY
    response_header_enabled: bool = True


def get_current_trace_id() -> str | None:
    """读取当前上下文中的 trace_id。

    :return: 当前上下文中的 trace_id；不存在时返回 ``None``。
    :rtype: str | None
    """

    return _TRACE_ID_CONTEXT.get()


def _build_uuidv7_hex() -> str:
    """生成 UUIDv7 的 32 位小写 hex 字符串。

    :return: UUIDv7 的无连字符 hex 字符串。
    :rtype: str
    """

    unix_ts_ms = (time_ns() // 1_000_000) & ((1 << _UUIDV7_TIMESTAMP_BITS) - 1)
    rand_a = randbits(_UUIDV7_RAND_A_BITS)
    rand_b = randbits(_UUIDV7_RAND_B_BITS)
    uuid_int = (
        (unix_ts_ms << 80)
        | (_UUIDV7_VERSION << 76)
        | (rand_a << 64)
        | (_UUID_VARIANT_RFC_9562 << 62)
        | rand_b
    )
    return UUID(int=uuid_int).hex


def _uuid7_trace_id_factory(prefix: str = "") -> TraceIdFactory:
    """创建基于 UUIDv7 的 trace_id 生成闭包。

    :param prefix: 可选 trace_id 前缀。
    :type prefix: str
    :return: 用于生成 trace_id 的闭包。
    :rtype: TraceIdFactory
    """

    def build_trace_id() -> str:
        """生成一个新的 trace_id。

        :return: 新生成的 trace_id。
        :rtype: str
        """

        return f"{prefix}{_build_uuidv7_hex()}"

    return build_trace_id


def _normalize_header_name(header_name: str) -> bytes:
    """规范化请求头名称。

    :param header_name: 原始请求头名称。
    :type header_name: str
    :return: 小写后的 ASCII 字节串请求头名称。
    :rtype: bytes
    """

    return header_name.strip().lower().encode("ascii")


def _get_header_value(scope: Scope, header_name: bytes) -> str | None:
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


def _ensure_state(scope: Scope) -> MutableMapping[str, Any]:
    """确保 ASGI scope 中存在可写的 state 映射。

    :param scope: ASGI 请求 scope。
    :type scope: Scope
    :return: 可写的 state 映射。
    :rtype: MutableMapping[str, Any]
    """

    state = scope.get("state")
    if isinstance(state, MutableMapping):
        return state

    created_state: MutableMapping[str, Any] = {}
    scope["state"] = created_state
    return created_state


def _with_response_header(
    message: Message,
    *,
    header_name: bytes,
    trace_id: str,
) -> Message:
    """返回写入 trace_id 响应头后的 ASGI 消息。

    :param message: 原始 ASGI 消息。
    :type message: Message
    :param header_name: 已规范化的小写响应头名称。
    :type header_name: bytes
    :param trace_id: 本次请求的 trace_id。
    :type trace_id: str
    :return: 写入响应头后的 ASGI 消息。
    :rtype: Message
    """

    headers = list(cast(tuple[HeaderPair, ...], message.get("headers", ())))
    filtered_headers = [
        (name, value) for name, value in headers if name.lower() != header_name
    ]
    filtered_headers.append((header_name, trace_id.encode("latin-1")))

    enriched_message = dict(message)
    enriched_message["headers"] = filtered_headers
    return enriched_message


class TraceIdMiddleware:
    """ASGI trace_id 注入与透传中间件。"""

    def __init__(  # noqa: PLR0913
        self,
        app: AsgiApp,
        *,
        header_name: str = _DEFAULT_TRACE_ID_HEADER,
        state_key: str = _DEFAULT_TRACE_ID_STATE_KEY,
        response_header_enabled: bool = True,
        trace_id_factory: TraceIdFactory | None = None,
        settings: TraceIdMiddlewareSettings | None = None,
    ) -> None:
        """初始化 trace_id 中间件。

        :param app: 下游 ASGI 应用。
        :type app: AsgiApp
        :param header_name: 承载 trace_id 的请求头名称。
        :type header_name: str
        :param state_key: 写入 ASGI ``scope["state"]`` 的键名。
        :type state_key: str
        :param response_header_enabled: 是否在响应头回写 trace_id。
        :type response_header_enabled: bool
        :param trace_id_factory: 可选 trace_id 生成函数。
        :type trace_id_factory: TraceIdFactory | None
        :param settings: 可选 trace_id 中间件配置；传入时优先使用。
        :type settings: TraceIdMiddlewareSettings | None
        :return: 无返回值。
        :rtype: None
        """

        resolved_settings = settings or TraceIdMiddlewareSettings(
            header_name=header_name,
            state_key=state_key,
            response_header_enabled=response_header_enabled,
        )
        self._app = app
        self._header_name = _normalize_header_name(resolved_settings.header_name)
        self._state_key = resolved_settings.state_key
        self._response_header_enabled = resolved_settings.response_header_enabled
        self._trace_id_factory = trace_id_factory or _uuid7_trace_id_factory()

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

        trace_id = self._resolve_trace_id(scope)
        state = _ensure_state(scope)
        state[self._state_key] = trace_id

        token = _TRACE_ID_CONTEXT.set(trace_id)
        try:
            await self._app(scope, receive, self._wrap_send(send, trace_id))
        finally:
            _TRACE_ID_CONTEXT.reset(token)

    def _resolve_trace_id(self, scope: Scope) -> str:
        """解析或生成本次请求的 trace_id。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :return: 本次请求使用的 trace_id。
        :rtype: str
        """

        return _get_header_value(scope, self._header_name) or self._trace_id_factory()

    def _wrap_send(self, send: Send, trace_id: str) -> Send:
        """创建会向响应头写入 trace_id 的 send 闭包。

        :param send: 原始 ASGI send 回调。
        :type send: Send
        :param trace_id: 本次请求的 trace_id。
        :type trace_id: str
        :return: 包装后的 ASGI send 回调。
        :rtype: Send
        """

        async def send_with_trace_id(message: Message) -> None:
            """发送可能带有 trace_id 响应头的 ASGI 消息。

            :param message: ASGI 消息。
            :type message: Message
            :return: 无返回值。
            :rtype: None
            """

            if (
                self._response_header_enabled
                and message.get("type") == "http.response.start"
            ):
                await send(
                    _with_response_header(
                        message,
                        header_name=self._header_name,
                        trace_id=trace_id,
                    )
                )
                return

            await send(message)

        return send_with_trace_id


__all__: Final[tuple[str, ...]] = (
    "AsgiApp",
    "HeaderPair",
    "Message",
    "Receive",
    "Scope",
    "Send",
    "TraceIdFactory",
    "TraceIdMiddleware",
    "TraceIdMiddlewareSettings",
    "get_current_trace_id",
)
