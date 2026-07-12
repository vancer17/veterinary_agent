##################################################################################################
# 文件: src/veterinary_agent/app/exception_handlers.py
# 作用: 注册 FastAPI 全局异常处理器，将框架层异常映射为项目统一错误响应结构。
# 边界: 仅处理 ASGI / FastAPI 框架层异常外壳，不替代 ApiIngress 业务错误映射与编排错误处理。
##################################################################################################

from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from veterinary_agent.api_ingress import (
    ApiIngressErrorResponseSource,
    ErrorDetailDto,
    ErrorResponseDto,
    INTERNAL_ERROR_SOURCE,
    IngressErrorCode,
    build_api_ingress_error_response,
)
from veterinary_agent.core import APP_STATE_KEY
from veterinary_agent.config import ApiIngressSettings

REQUIRED_CONTEXT_FIELDS = frozenset(("user_id", "session_id", "pet_id"))
MISSING_CONTEXT_ERROR_TYPES = frozenset(("missing", "string_too_short"))


def _get_api_ingress_settings(request: Request) -> ApiIngressSettings | None:
    """从应用状态读取 API 接入组件配置。

    :param request: 当前 HTTP 请求对象。
    :return: 已加载的 API 接入组件配置；未初始化时返回 None。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    settings = getattr(app_state, "settings", None)
    if isinstance(settings, ApiIngressSettings):
        return settings
    return None


def _get_identity_header_names(request: Request) -> tuple[str, str]:
    """获取请求 ID 与链路 ID 的 Header 名称。

    :param request: 当前 HTTP 请求对象。
    :return: 请求 ID Header 名称与链路 ID Header 名称。
    """

    settings = _get_api_ingress_settings(request)
    if settings is not None:
        return (
            settings.request_identity.request_id_header,
            settings.request_identity.trace_id_header,
        )
    return "X-Request-ID", "X-Trace-ID"


def _get_header_value(request: Request, header_name: str, fallback: str) -> str:
    """从请求头读取指定值。

    :param request: 当前 HTTP 请求对象。
    :param header_name: 需要读取的 HTTP Header 名称。
    :param fallback: Header 缺失时使用的兜底值。
    :return: 请求头中的值或兜底值。
    """

    value = request.headers.get(header_name)
    if value:
        return value
    return fallback


def _build_error_response(
    request: Request,
    code: IngressErrorCode,
    public_message: str | None,
    details: list[ErrorDetailDto] | None = None,
    diagnostic_message: str | None = None,
    source: ApiIngressErrorResponseSource = "client",
) -> ErrorResponseDto:
    """构建统一错误响应 DTO。

    :param request: 当前 HTTP 请求对象。
    :param code: 入口层错误码。
    :param public_message: 默认对外展示的稳定错误消息。
    :param details: 字段级或依赖级错误明细。
    :param diagnostic_message: 允许详细诊断时可展示的错误消息。
    :param source: 错误来源分类，用于决定是否隐藏内部明细。
    :return: 统一错误响应 DTO。
    """

    settings = _get_api_ingress_settings(request)
    request_id_header, trace_id_header = _get_identity_header_names(request)
    request_id = _get_header_value(request, request_id_header, "req_unavailable")
    trace_id = _get_header_value(request, trace_id_header, "trace_unavailable")
    if settings is not None:
        return build_api_ingress_error_response(
            settings=settings,
            code=code,
            request_id=request_id,
            trace_id=trace_id,
            public_message=public_message,
            diagnostic_message=diagnostic_message,
            details=details,
            source=source,
        )
    return ErrorResponseDto(
        code=code,
        message=public_message or diagnostic_message or "request failed",
        request_id=request_id,
        trace_id=trace_id,
        details=details,
    )


def _normalize_error_location(location: object) -> list[str]:
    """规范化 FastAPI / Pydantic 错误字段路径。

    :param location: FastAPI / Pydantic 返回的原始错误路径。
    :return: 去除 body 前缀后的字段路径片段列表。
    """

    if isinstance(location, tuple | list):
        parts = [str(part) for part in location]
    else:
        parts = [str(location)]
    if parts and parts[0] == "body":
        return parts[1:]
    return parts


def _format_error_field(location: object) -> str:
    """格式化 FastAPI / Pydantic 错误字段路径。

    :param location: FastAPI / Pydantic 返回的原始错误路径。
    :return: 可放入错误响应 details.field 的字段路径。
    """

    parts = _normalize_error_location(location)
    if not parts:
        return "body"
    return ".".join(parts)


def _is_missing_required_context_error(error: dict[str, object]) -> bool:
    """判断校验错误是否属于必需上下文缺失。

    :param error: FastAPI / Pydantic 返回的单个错误对象。
    :return: 若错误属于 vet_context 必需字段缺失或空值，则返回 True。
    """

    error_type = str(error.get("type", ""))
    location = _normalize_error_location(error.get("loc", ()))
    if location == ["vet_context"] and error_type == "missing":
        return True
    return (
        len(location) >= 2
        and location[0] == "vet_context"
        and location[1] in REQUIRED_CONTEXT_FIELDS
        and error_type in MISSING_CONTEXT_ERROR_TYPES
    )


def _resolve_validation_error_mapping(
    errors: list[dict[str, object]],
) -> tuple[int, IngressErrorCode, str]:
    """解析请求校验错误对应的 HTTP 状态码与业务错误码。

    :param errors: FastAPI / Pydantic 返回的错误对象列表。
    :return: HTTP 状态码、入口层错误码和错误说明。
    """

    if any(_is_missing_required_context_error(error) for error in errors):
        return (
            422,
            IngressErrorCode.MISSING_REQUIRED_CONTEXT,
            "missing required context",
        )
    return 400, IngressErrorCode.INVALID_REQUEST, "request validation failed"


async def handle_request_validation_error(
    request: Request, exc: Exception
) -> JSONResponse:
    """处理 FastAPI 请求校验异常。

    :param request: 当前 HTTP 请求对象。
    :param exc: FastAPI 请求校验异常。
    :return: 使用项目统一错误结构包装后的 JSON 响应。
    """

    if isinstance(exc, RequestValidationError):
        errors = cast(list[dict[str, object]], exc.errors())
        details = [
            ErrorDetailDto(
                field=_format_error_field(error.get("loc", ())),
                reason=str(error["msg"]),
            )
            for error in errors
        ]
        status_code, code, message = _resolve_validation_error_mapping(errors)
    else:
        details = [ErrorDetailDto(reason=exc.__class__.__name__)]
        status_code = 400
        code = IngressErrorCode.INVALID_REQUEST
        message = "request validation failed"
    error_response = _build_error_response(
        request=request,
        code=code,
        public_message=message,
        details=details,
    )
    return JSONResponse(
        status_code=status_code, content=error_response.model_dump(mode="json")
    )


async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """处理未捕获异常。

    :param request: 当前 HTTP 请求对象。
    :param exc: 未捕获异常对象。
    :return: 使用项目统一错误结构包装后的 JSON 响应。
    """

    error_response = _build_error_response(
        request=request,
        code=IngressErrorCode.INTERNAL_ERROR,
        public_message=None,
        diagnostic_message="internal server error",
        details=[ErrorDetailDto(reason=exc.__class__.__name__)],
        source=INTERNAL_ERROR_SOURCE,
    )
    return JSONResponse(status_code=500, content=error_response.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    """注册 FastAPI 全局异常处理器。

    :param app: 需要注册异常处理器的 FastAPI 应用实例。
    :return: 无返回值。
    """

    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)


__all__: tuple[str, ...] = (
    "handle_request_validation_error",
    "handle_unhandled_exception",
    "register_exception_handlers",
)
