##################################################################################################
# 文件: src/veterinary_agent/api_ingress/request_parser.py
# 作用: 定义 API 接入组件请求体解析能力，将原始 HTTP body 在配置化超时内解析为外部请求 DTO。
# 边界: 仅处理 body 读取、JSON 解析、Pydantic DTO Validation 和入口错误映射，不执行身份解析、编排或业务判断。
##################################################################################################

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar, cast

from fastapi import Request
from pydantic import ValidationError

from veterinary_agent.api_ingress.dto import (
    AgentTurnRequestDto,
    ErrorDetailDto,
    ErrorResponseDto,
)
from veterinary_agent.api_ingress.error_response import (
    build_api_ingress_error_response,
)
from veterinary_agent.api_ingress.enums import IngressErrorCode
from veterinary_agent.config import ApiIngressSettings

REQUEST_ID_FALLBACK = "req_unavailable"
TRACE_ID_FALLBACK = "trace_unavailable"
REQUEST_PARSE_TIMEOUT_STATUS_CODE = 408
REQUIRED_CONTEXT_FIELDS = frozenset(("user_id", "session_id", "pet_id"))
MISSING_CONTEXT_ERROR_TYPES = frozenset(("missing", "string_too_short"))

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ApiIngressRequestParseFailure:
    """API 接入请求解析失败结果。"""

    status_code: int
    error_response: ErrorResponseDto


@dataclass(frozen=True, slots=True)
class ApiIngressRequestParseResult:
    """API 接入请求解析结果。"""

    turn_request: AgentTurnRequestDto | None
    failure: ApiIngressRequestParseFailure | None


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


def _build_detail(field: str, reason: str) -> ErrorDetailDto:
    """构建字段级错误明细。

    :param field: 发生错误的字段路径。
    :param reason: 错误原因。
    :return: 字段级错误明细 DTO。
    """

    return ErrorDetailDto(field=field, reason=reason)


def _build_failure(
    request: Request,
    settings: ApiIngressSettings,
    status_code: int,
    code: IngressErrorCode,
    message: str,
    details: list[ErrorDetailDto],
) -> ApiIngressRequestParseFailure:
    """构建 API 接入请求解析失败结果。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :param status_code: HTTP 状态码。
    :param code: 入口层错误码。
    :param message: 面向研发的错误说明。
    :param details: 字段级或依赖级错误明细。
    :return: API 接入请求解析失败结果。
    """

    request_id = _get_header_value(
        request,
        settings.request_identity.request_id_header,
        REQUEST_ID_FALLBACK,
    )
    trace_id = _get_header_value(
        request,
        settings.request_identity.trace_id_header,
        TRACE_ID_FALLBACK,
    )
    return ApiIngressRequestParseFailure(
        status_code=status_code,
        error_response=build_api_ingress_error_response(
            settings=settings,
            code=code,
            request_id=request_id,
            trace_id=trace_id,
            public_message=message,
            details=details,
        ),
    )


def _normalize_error_location(location: object) -> list[str]:
    """规范化 Pydantic 错误字段路径。

    :param location: Pydantic 返回的原始错误路径。
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
    """格式化 Pydantic 错误字段路径。

    :param location: Pydantic 返回的原始错误路径。
    :return: 可放入错误响应 details.field 的字段路径。
    """

    parts = _normalize_error_location(location)
    if not parts:
        return "body"
    return ".".join(parts)


def _is_missing_required_context_error(error: dict[str, object]) -> bool:
    """判断校验错误是否属于必需上下文缺失。

    :param error: Pydantic 返回的单个错误对象。
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
    """解析 DTO Validation 错误对应的 HTTP 状态码与业务错误码。

    :param errors: Pydantic 返回的错误对象列表。
    :return: HTTP 状态码、入口层错误码和错误说明。
    """

    if any(_is_missing_required_context_error(error) for error in errors):
        return (
            422,
            IngressErrorCode.MISSING_REQUIRED_CONTEXT,
            "missing required context",
        )
    return 400, IngressErrorCode.INVALID_REQUEST, "request validation failed"


def _build_validation_failure(
    request: Request,
    settings: ApiIngressSettings,
    exc: ValidationError,
) -> ApiIngressRequestParseFailure:
    """根据 Pydantic ValidationError 构建请求解析失败结果。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :param exc: Pydantic DTO Validation 异常。
    :return: API 接入请求解析失败结果。
    """

    errors = cast(list[dict[str, object]], exc.errors())
    details = [
        ErrorDetailDto(
            field=_format_error_field(error.get("loc", ())),
            reason=str(error["msg"]),
        )
        for error in errors
    ]
    status_code, code, message = _resolve_validation_error_mapping(errors)
    return _build_failure(
        request=request,
        settings=settings,
        status_code=status_code,
        code=code,
        message=message,
        details=details,
    )


def _decode_turn_request(body: bytes) -> AgentTurnRequestDto:
    """将原始请求体解析为外部一轮对话请求 DTO。

    :param body: 原始 HTTP 请求体字节。
    :return: 外部一轮 Agent 对话请求 DTO。
    :raises json.JSONDecodeError: 当请求体不是合法 JSON 时抛出。
    :raises UnicodeDecodeError: 当请求体无法按 JSON 支持的编码解析时抛出。
    :raises ValidationError: 当 JSON 结构不满足 DTO 契约时抛出。
    """

    payload = json.loads(body)
    return AgentTurnRequestDto.model_validate(payload)


def _remaining_timeout_seconds(started_at: float, timeout_seconds: float) -> float:
    """计算解析阶段剩余超时时间。

    :param started_at: 解析阶段开始时的单调时钟时间。
    :param timeout_seconds: 配置的解析总超时时间。
    :return: 剩余可用秒数；已耗尽时返回 0。
    """

    elapsed_seconds = monotonic() - started_at
    return max(timeout_seconds - elapsed_seconds, 0.0)


async def _await_with_timeout(
    awaitable: Awaitable[T],
    timeout_seconds: float,
) -> T:
    """在指定超时时间内等待异步任务完成。

    :param awaitable: 需要等待的异步任务。
    :param timeout_seconds: 允许等待的最大秒数。
    :return: 异步任务返回值。
    :raises TimeoutError: 当超时时间耗尽时抛出。
    """

    if timeout_seconds <= 0:
        raise TimeoutError
    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


async def parse_agent_turn_request(
    request: Request,
    settings: ApiIngressSettings,
) -> ApiIngressRequestParseResult:
    """在配置化超时内解析 API 接入一轮对话请求。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :return: API 接入请求解析结果；失败时携带统一错误响应。
    """

    started_at = monotonic()
    timeout_seconds = settings.request_limits.parse_timeout_seconds
    try:
        body = await _await_with_timeout(request.body(), timeout_seconds)
        if len(body) > settings.request_limits.max_body_bytes:
            return ApiIngressRequestParseResult(
                turn_request=None,
                failure=_build_failure(
                    request=request,
                    settings=settings,
                    status_code=413,
                    code=IngressErrorCode.PAYLOAD_TOO_LARGE,
                    message="request body is too large",
                    details=[_build_detail("body", "max_body_bytes_exceeded")],
                ),
            )
        turn_request = await _await_with_timeout(
            asyncio.to_thread(_decode_turn_request, body),
            _remaining_timeout_seconds(started_at, timeout_seconds),
        )
    except TimeoutError:
        return ApiIngressRequestParseResult(
            turn_request=None,
            failure=_build_failure(
                request=request,
                settings=settings,
                status_code=REQUEST_PARSE_TIMEOUT_STATUS_CODE,
                code=IngressErrorCode.INVALID_REQUEST,
                message="request parsing timed out",
                details=[_build_detail("body", "parse_timeout_exceeded")],
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ApiIngressRequestParseResult(
            turn_request=None,
            failure=_build_failure(
                request=request,
                settings=settings,
                status_code=400,
                code=IngressErrorCode.INVALID_REQUEST,
                message="invalid json body",
                details=[_build_detail("body", "invalid_json")],
            ),
        )
    except ValidationError as exc:
        return ApiIngressRequestParseResult(
            turn_request=None,
            failure=_build_validation_failure(
                request=request,
                settings=settings,
                exc=exc,
            ),
        )
    return ApiIngressRequestParseResult(turn_request=turn_request, failure=None)


__all__: tuple[str, ...] = (
    "ApiIngressRequestParseFailure",
    "ApiIngressRequestParseResult",
    "parse_agent_turn_request",
)
