#
# 模块：veterinary_agent.http
# 用途：HTTP API 基础设施辅助工具的公开包入口。
# 层级：L0 HTTP 适配层。
# 契约：跨包使用方应从这里导入公开符号。
#

from __future__ import annotations

from .errors import (
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
from .middleware import (
    AsgiApp,
    HeaderPair,
    Message,
    PathPredicate,
    Receive,
    RequestLogMiddleware,
    RequestLogMiddlewareSettings,
    RequestLogRecord,
    Scope,
    Send,
    ServiceKeyMiddleware,
    ServiceKeyMiddlewareSettings,
    TraceIdFactory,
    TraceIdMiddleware,
    TraceIdMiddlewareSettings,
    get_current_trace_id,
)
from .settings import (
    HttpMiddlewareSettings,
    HttpSettings,
    LogLevelName,
    load_http_middleware_settings_from_env,
    load_http_settings,
)

__all__: tuple[str, ...] = (
    "DEFAULT_ERROR_RESPONSE_FACTORY",
    "AsgiApp",
    "ErrorBody",
    "ErrorCode",
    "ErrorResponse",
    "ErrorResponseFactory",
    "ErrorSpec",
    "ErrorType",
    "HeaderPair",
    "HttpApiError",
    "HttpMiddlewareSettings",
    "HttpSettings",
    "LogLevelName",
    "Message",
    "OrchestrationUnavailableError",
    "PathPredicate",
    "PetNotAuthorizedError",
    "Receive",
    "RequestLogMiddleware",
    "RequestLogMiddlewareSettings",
    "RequestLogRecord",
    "RequestValidationError",
    "ResponseHeaders",
    "Scope",
    "Send",
    "ServiceKeyError",
    "ServiceKeyMiddleware",
    "ServiceKeyMiddlewareSettings",
    "TraceIdFactory",
    "TraceIdMiddleware",
    "TraceIdMiddlewareSettings",
    "build_error_response",
    "get_current_trace_id",
    "load_http_middleware_settings_from_env",
    "load_http_settings",
)
