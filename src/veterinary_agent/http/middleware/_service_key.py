#########################################################################
# 模块：veterinary_agent.http.middleware._service_key
# 用途：实现 L0 HTTP 入口的服务间密钥校验中间件。
# 层级：L0 HTTP 适配层；ASGI 原生中间件。
# 契约：AGENT_SERVICE_KEY 非空时校验 X-Service-Key；失败返回 403。
# 备注：本模块为私有实现。请从 veterinary_agent.http.middleware 或
#        veterinary_agent.http 导入公开符号。
#########################################################################

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict

from veterinary_agent.http.errors import (
    DEFAULT_ERROR_RESPONSE_FACTORY,
    ErrorResponse,
    ErrorResponseFactory,
)

type Scope = MutableMapping[str, Any]
type HeaderPair = tuple[bytes, bytes]
type Message = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]
type PathPredicate = Callable[[str], bool]

_DEFAULT_PROTECTED_PATH_PREFIX: Final[str] = "/v1/"
_DEFAULT_SERVICE_KEY_HEADER: Final[str] = "x-service-key"

_CONTENT_TYPE_JSON: Final[tuple[HeaderPair, ...]] = (
    (b"content-type", b"application/json; charset=utf-8"),
)


class ServiceKeyMiddlewareSettings(BaseModel):
    """服务间密钥中间件配置。

    :param service_key: 期望的服务间密钥；空字符串表示关闭校验。
    :type service_key: str
    :param header_name: 承载服务间密钥的请求头名称。
    :type header_name: str
    :param protected_path_prefix: 默认受保护的路径前缀。
    :type protected_path_prefix: str
    :param ready_requires_service_key: ``/ready`` 是否也需要服务间密钥。
    :type ready_requires_service_key: bool
    """

    model_config = ConfigDict(frozen=True)

    service_key: str = ""
    header_name: str = _DEFAULT_SERVICE_KEY_HEADER
    protected_path_prefix: str = _DEFAULT_PROTECTED_PATH_PREFIX
    ready_requires_service_key: bool = False


def _default_path_predicate(
    protected_path_prefix: str,
    *,
    ready_requires_service_key: bool,
) -> PathPredicate:
    """创建默认路径保护判定闭包。

    :param protected_path_prefix: 默认受保护的路径前缀。
    :type protected_path_prefix: str
    :param ready_requires_service_key: ``/ready`` 是否也需要服务间密钥。
    :type ready_requires_service_key: bool
    :return: 用于判定路径是否需要密钥校验的闭包。
    :rtype: PathPredicate
    """

    normalized_prefix = protected_path_prefix or _DEFAULT_PROTECTED_PATH_PREFIX

    def is_protected(path: str) -> bool:
        """判断请求路径是否需要服务间密钥。

        :param path: ASGI 请求路径。
        :type path: str
        :return: 需要校验时返回 ``True``，否则返回 ``False``。
        :rtype: bool
        """

        if path.startswith(normalized_prefix):
            return True
        return ready_requires_service_key and path == "/ready"

    return is_protected


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
    :return: 请求头值；不存在时返回 ``None``。
    :rtype: str | None
    """

    headers = cast(tuple[tuple[bytes, bytes], ...], scope.get("headers", ()))
    for name, value in headers:
        if name.lower() == header_name:
            return value.decode("latin-1")
    return None


class ServiceKeyMiddleware:
    """ASGI 服务间密钥校验中间件。"""

    def __init__(  # noqa: PLR0913
        self,
        app: AsgiApp,
        *,
        service_key: str = "",
        header_name: str = _DEFAULT_SERVICE_KEY_HEADER,
        protected_path_prefix: str = _DEFAULT_PROTECTED_PATH_PREFIX,
        ready_requires_service_key: bool = False,
        path_predicate: PathPredicate | None = None,
        error_factory: ErrorResponseFactory = DEFAULT_ERROR_RESPONSE_FACTORY,
        settings: ServiceKeyMiddlewareSettings | None = None,
    ) -> None:
        """初始化服务间密钥中间件。

        :param app: 下游 ASGI 应用。
        :type app: AsgiApp
        :param service_key: 期望的服务间密钥；空字符串表示关闭校验。
        :type service_key: str
        :param header_name: 承载服务间密钥的请求头名称。
        :type header_name: str
        :param protected_path_prefix: 默认受保护的路径前缀。
        :type protected_path_prefix: str
        :param ready_requires_service_key: ``/ready`` 是否也需要服务间密钥。
        :type ready_requires_service_key: bool
        :param path_predicate: 可选的路径保护判定函数。
        :type path_predicate: PathPredicate | None
        :param error_factory: 错误响应工厂。
        :type error_factory: ErrorResponseFactory
        :param settings: 可选服务间密钥中间件配置；传入时优先使用。
        :type settings: ServiceKeyMiddlewareSettings | None
        :return: 无返回值。
        :rtype: None
        """

        resolved_settings = settings or ServiceKeyMiddlewareSettings(
            service_key=service_key,
            header_name=header_name,
            protected_path_prefix=protected_path_prefix,
            ready_requires_service_key=ready_requires_service_key,
        )
        self._app = app
        self._service_key = resolved_settings.service_key
        self._header_name = _normalize_header_name(resolved_settings.header_name)
        self._path_predicate = path_predicate or _default_path_predicate(
            resolved_settings.protected_path_prefix,
            ready_requires_service_key=resolved_settings.ready_requires_service_key,
        )
        self._error_factory = error_factory

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

        if not self._should_check(scope):
            await self._app(scope, receive, send)
            return

        actual_key = _get_header_value(scope, self._header_name)
        if actual_key != self._service_key:
            await self._send_error(send, self._error_factory.invalid_service_key())
            return

        await self._app(scope, receive, send)

    def _should_check(self, scope: Scope) -> bool:
        """判断当前请求是否需要密钥校验。

        :param scope: ASGI 请求 scope。
        :type scope: Scope
        :return: 需要校验时返回 ``True``，否则返回 ``False``。
        :rtype: bool
        """

        if not self._service_key:
            return False
        if scope.get("type") != "http":
            return False

        path = str(scope.get("path") or "")
        return self._path_predicate(path)

    async def _send_error(self, send: Send, response: ErrorResponse) -> None:
        """发送服务间密钥错误响应。

        :param send: ASGI send 回调。
        :type send: Send
        :param response: 框架无关错误响应。
        :type response: ErrorResponse
        :return: 无返回值。
        :rtype: None
        """

        body = json.dumps(response.body, ensure_ascii=False).encode("utf-8")
        headers: list[HeaderPair] = list(_CONTENT_TYPE_JSON)
        headers.extend(
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in response.headers.items()
        )

        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__: Final[tuple[str, ...]] = (
    "AsgiApp",
    "Message",
    "PathPredicate",
    "Receive",
    "Scope",
    "Send",
    "ServiceKeyMiddleware",
    "ServiceKeyMiddlewareSettings",
)
