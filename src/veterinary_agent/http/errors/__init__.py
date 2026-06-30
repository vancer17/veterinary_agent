#
# 模块：veterinary_agent.http.errors
# 用途：HTTP API 错误响应辅助工具的公开包入口。
# 层级：L0 HTTP 适配层支撑。
# 契约：使用方应从本包导入，避免直接引用私有实现模块。
#

from __future__ import annotations

from ._factory import (
    DEFAULT_ERROR_RESPONSE_FACTORY,
    ErrorBody,
    ErrorCode,
    ErrorResponse,
    ErrorResponseFactory,
    ErrorSpec,
    ErrorType,
    HttpApiError,
    OrchestrationUnavailableError,
    PetNotAuthorizedError,
    RequestValidationError,
    ResponseHeaders,
    ServiceKeyError,
    build_error_response,
)

__all__: tuple[str, ...] = (
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
