#########################################################################
# 模块：veterinary_agent.http.errors._factory
# 用途：构造符合公开契约的 HTTP Chat API 错误响应载荷。
# 层级：L0 HTTP 适配层支撑；框架无关核心。
# 契约：docs/09_api/api/http-api-api.md 错误响应形状。
# 备注：本模块为私有实现。请从 veterinary_agent.http.errors 或
#        veterinary_agent.http 导入公开符号。
#########################################################################

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

type ErrorType = Literal[
    "invalid_request_error",
    "authentication_error",
    "permission_error",
    "server_error",
]
type ErrorBody = dict[str, dict[str, str | None]]
type ResponseHeaders = Mapping[str, str]
type MessageBuilder = Callable[[str | None], str]


class ErrorCode(StrEnum):
    """HTTP Chat API 对外暴露的稳定 ``error.code`` 值。"""

    INVALID_JSON = "invalid_json"
    MISSING_USER_ID = "missing_user_id"
    MISSING_SESSION_ID = "missing_session_id"
    MISSING_PET_ID = "missing_pet_id"
    EMPTY_MESSAGES = "empty_messages"
    MISSING_USER_MESSAGE = "missing_user_message"
    EMPTY_USER_CONTENT = "empty_user_content"
    UNSUPPORTED_FIELD = "unsupported_field"
    INVALID_SERVICE_KEY = "invalid_service_key"
    PET_NOT_AUTHORIZED = "pet_not_authorized"
    ORCHESTRATION_UNAVAILABLE = "orchestration_unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """单个 HTTP API 错误码的契约元数据。"""

    status_code: int
    error_type: ErrorType
    message: str
    param: str | None = None


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    """``ErrorResponseFactory`` 返回的框架无关错误响应。"""

    status_code: int
    body: ErrorBody
    headers: ResponseHeaders = MappingProxyType({})


class HttpApiError(Exception):
    """应转换为契约错误响应的 HTTP API 异常基类。

    :param code: 稳定 API 错误码。
    :type code: ErrorCode | str
    :param message: 可选的错误消息覆盖值。
    :type message: str | None
    :param param: 与错误相关的可选请求参数名。
    :type param: str | None
    """

    def __init__(
        self,
        code: ErrorCode | str,
        *,
        message: str | None = None,
        param: str | None = None,
    ) -> None:
        self.code: ErrorCode = ErrorCode(code)
        self.message: str | None = message
        self.param: str | None = param
        super().__init__(message or self.code.value)


class RequestValidationError(HttpApiError):
    """映射为 4xx 契约响应的请求校验错误。"""


class ServiceKeyError(HttpApiError):
    """服务间密钥鉴权错误。

    :param message: 可选的错误消息覆盖值。
    :type message: str | None
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ErrorCode.INVALID_SERVICE_KEY, message=message)


class PetNotAuthorizedError(HttpApiError):
    """严格模式下的宠物访问授权错误。

    :param message: 可选的错误消息覆盖值。
    :type message: str | None
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ErrorCode.PET_NOT_AUTHORIZED, message=message)


class OrchestrationUnavailableError(HttpApiError):
    """无法降级处理的编排依赖错误。

    :param message: 可选的错误消息覆盖值。
    :type message: str | None
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ErrorCode.ORCHESTRATION_UNAVAILABLE, message=message)


def _message_for_param(template: str) -> MessageBuilder:
    """创建可选使用请求参数名的错误消息构造器。

    :param template: 接收命名 ``param`` 值的消息模板。
    :type template: str
    :return: 用于渲染稳定错误消息的闭包。
    :rtype: MessageBuilder
    """

    def build_message(param: str | None) -> str:
        """渲染已配置的消息模板。

        :param param: 可用时传入的请求参数名。
        :type param: str | None
        :return: 渲染后的错误消息。
        :rtype: str
        """

        return template.format(param=param or "field")

    return build_message


_DEFAULT_SPECS: Final[Mapping[ErrorCode, ErrorSpec]] = MappingProxyType(
    {
        ErrorCode.INVALID_JSON: ErrorSpec(
            status_code=400,
            error_type="invalid_request_error",
            message="请求体必须是合法 JSON。",
        ),
        ErrorCode.MISSING_USER_ID: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="user_id 为必填项。",
            param="user_id",
        ),
        ErrorCode.MISSING_SESSION_ID: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="session_id 为必填项。",
            param="session_id",
        ),
        ErrorCode.MISSING_PET_ID: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="pet_id 为必填项。",
            param="pet_id",
        ),
        ErrorCode.EMPTY_MESSAGES: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="messages 至少需要包含一条消息。",
            param="messages",
        ),
        ErrorCode.MISSING_USER_MESSAGE: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="messages 至少需要包含一条用户消息。",
            param="messages",
        ),
        ErrorCode.EMPTY_USER_CONTENT: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="最后一条用户消息的 content 为必填项。",
            param="messages",
        ),
        ErrorCode.UNSUPPORTED_FIELD: ErrorSpec(
            status_code=422,
            error_type="invalid_request_error",
            message="请求包含不支持的字段。",
        ),
        ErrorCode.INVALID_SERVICE_KEY: ErrorSpec(
            status_code=403,
            error_type="authentication_error",
            message="服务间密钥无效。",
        ),
        ErrorCode.PET_NOT_AUTHORIZED: ErrorSpec(
            status_code=403,
            error_type="permission_error",
            message="用户无权访问当前选择的宠物。",
            param="pet_id",
        ),
        ErrorCode.ORCHESTRATION_UNAVAILABLE: ErrorSpec(
            status_code=503,
            error_type="server_error",
            message="编排服务当前不可用。",
        ),
        ErrorCode.INTERNAL_ERROR: ErrorSpec(
            status_code=500,
            error_type="server_error",
            message="服务器内部错误。",
        ),
    }
)

_PARAM_MESSAGE_BUILDERS: Final[Mapping[ErrorCode, MessageBuilder]] = MappingProxyType(
    {ErrorCode.UNSUPPORTED_FIELD: _message_for_param("请求字段 '{param}' 不受支持。")}
)


class ErrorResponseFactory:
    """构造框架无关的 HTTP API 错误响应。"""

    def __init__(
        self,
        specs: Mapping[ErrorCode, ErrorSpec] | None = None,
    ) -> None:
        """初始化错误响应工厂。

        :param specs: 可选的错误规格覆盖映射。
        :type specs: Mapping[ErrorCode, ErrorSpec] | None
        :return: 无返回值。
        :rtype: None
        """

        self._specs: Mapping[ErrorCode, ErrorSpec] = specs or _DEFAULT_SPECS

    def build(
        self,
        code: ErrorCode | str,
        *,
        message: str | None = None,
        param: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """根据稳定 API 错误码构造错误响应。

        :param code: 稳定 API 错误码。
        :type code: ErrorCode | str
        :param message: 可选的错误消息覆盖值。
        :type message: str | None
        :param param: 与错误相关的可选请求参数名。
        :type param: str | None
        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: 框架无关的错误响应。
        :rtype: ErrorResponse
        :raises ValueError: 当 ``code`` 未知时抛出。
        """

        error_code = ErrorCode(code)
        spec = self._specs[error_code]
        resolved_param = param if param is not None else spec.param
        resolved_message = message or self._build_message(
            error_code,
            spec,
            resolved_param,
        )

        return ErrorResponse(
            status_code=spec.status_code,
            body={
                "error": {
                    "message": resolved_message,
                    "type": spec.error_type,
                    "code": error_code.value,
                    "param": resolved_param,
                }
            },
            headers=MappingProxyType(dict(headers or {})),
        )

    def from_exception(
        self,
        exc: BaseException,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """根据异常构造错误响应。

        :param exc: 中间件、处理器或编排层抛出的异常。
        :type exc: BaseException
        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: 框架无关的错误响应。
        :rtype: ErrorResponse
        """

        if isinstance(exc, HttpApiError):
            return self.build(
                exc.code,
                message=exc.message,
                param=exc.param,
                headers=headers,
            )
        return self.build(ErrorCode.INTERNAL_ERROR, headers=headers)

    def validation(
        self,
        code: ErrorCode | str,
        *,
        message: str | None = None,
        param: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """构造请求校验错误响应。

        :param code: 校验错误码。
        :type code: ErrorCode | str
        :param message: 可选的错误消息覆盖值。
        :type message: str | None
        :param param: 与错误相关的可选请求参数名。
        :type param: str | None
        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: 框架无关的校验错误响应。
        :rtype: ErrorResponse
        """

        return self.build(code, message=message, param=param, headers=headers)

    def invalid_json(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """构造 JSON 非法错误响应。

        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: ``invalid_json`` 错误响应。
        :rtype: ErrorResponse
        """

        return self.build(ErrorCode.INVALID_JSON, headers=headers)

    def invalid_service_key(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """构造服务间密钥无效错误响应。

        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: ``invalid_service_key`` 错误响应。
        :rtype: ErrorResponse
        """

        return self.build(ErrorCode.INVALID_SERVICE_KEY, headers=headers)

    def internal_error(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ErrorResponse:
        """构造服务器内部错误响应。

        :param headers: 可选响应头。
        :type headers: Mapping[str, str] | None
        :return: ``internal_error`` 错误响应。
        :rtype: ErrorResponse
        """

        return self.build(ErrorCode.INTERNAL_ERROR, headers=headers)

    def _build_message(
        self,
        code: ErrorCode,
        spec: ErrorSpec,
        param: str | None,
    ) -> str:
        """解析错误码对应的响应消息。

        :param code: 稳定 API 错误码。
        :type code: ErrorCode
        :param spec: 错误规格。
        :type spec: ErrorSpec
        :param param: 与错误相关的请求参数名。
        :type param: str | None
        :return: 响应消息。
        :rtype: str
        """

        builder = _PARAM_MESSAGE_BUILDERS.get(code)
        if builder is None:
            return spec.message
        return builder(param)


def build_error_response(
    code: ErrorCode | str,
    *,
    message: str | None = None,
    param: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> ErrorResponse:
    """使用默认工厂构造错误响应。

    :param code: 稳定 API 错误码。
    :type code: ErrorCode | str
    :param message: 可选的错误消息覆盖值。
    :type message: str | None
    :param param: 与错误相关的可选请求参数名。
    :type param: str | None
    :param headers: 可选响应头。
    :type headers: Mapping[str, str] | None
    :return: 框架无关的错误响应。
    :rtype: ErrorResponse
    """

    return DEFAULT_ERROR_RESPONSE_FACTORY.build(
        code,
        message=message,
        param=param,
        headers=headers,
    )


DEFAULT_ERROR_RESPONSE_FACTORY: Final[ErrorResponseFactory] = ErrorResponseFactory()


__all__: Final[tuple[str, ...]] = (
    "DEFAULT_ERROR_RESPONSE_FACTORY",
    "ErrorBody",
    "ErrorCode",
    "ErrorResponse",
    "ErrorResponseFactory",
    "ErrorSpec",
    "ErrorType",
    "HttpApiError",
    "OrchestrationUnavailableError",
    "PetNotAuthorizedError",
    "RequestValidationError",
    "ResponseHeaders",
    "ServiceKeyError",
    "build_error_response",
)
