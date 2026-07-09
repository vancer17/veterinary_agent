##################################################################################################
# 文件: src/veterinary_agent/api_ingress/identity.py
# 作用: 定义 API 接入组件的请求身份解析能力，统一生成或透传 request_id 与 trace_id。
# 边界: 仅处理入口请求身份字段的来源选择、格式校验和缺失补齐，不执行鉴权、编排或业务留痕。
##################################################################################################

import re
from dataclasses import dataclass
from secrets import randbits
from time import time_ns
from typing import Final, Literal, TypeAlias
from uuid import UUID

from fastapi import Request

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

IdentityValueSource: TypeAlias = Literal["header", "body", "generated"]

UUIDV7_TIMESTAMP_BITS: Final[int] = 48
UUIDV7_RANDOM_A_BITS: Final[int] = 12
UUIDV7_RANDOM_B_BITS: Final[int] = 62
UUIDV7_VERSION: Final[int] = 0x7
UUID_RFC4122_VARIANT: Final[int] = 0b10
NANOSECONDS_PER_MILLISECOND: Final[int] = 1_000_000


@dataclass(frozen=True, slots=True)
class RequestIdentityContext:
    """API 接入请求身份上下文。"""

    request_id: str
    trace_id: str
    request_id_source: IdentityValueSource
    trace_id_source: IdentityValueSource


@dataclass(frozen=True, slots=True)
class RequestIdentityResolutionFailure:
    """API 接入请求身份解析失败结果。"""

    status_code: int
    error_response: ErrorResponseDto


@dataclass(frozen=True, slots=True)
class RequestIdentityResolution:
    """API 接入请求身份解析结果。"""

    identity_context: RequestIdentityContext | None
    failure: RequestIdentityResolutionFailure | None


@dataclass(frozen=True, slots=True)
class _SingleIdentityResolution:
    """单个请求身份字段解析结果。"""

    value: str
    source: IdentityValueSource
    response_value: str
    details: list[ErrorDetailDto]


def _uuid7() -> UUID:
    """生成符合 UUIDv7 位布局的 UUID。

    :return: 基于当前毫秒时间戳和安全随机位生成的 UUIDv7。
    """

    timestamp_ms = (time_ns() // NANOSECONDS_PER_MILLISECOND) & (
        (1 << UUIDV7_TIMESTAMP_BITS) - 1
    )
    random_a = randbits(UUIDV7_RANDOM_A_BITS)
    random_b = randbits(UUIDV7_RANDOM_B_BITS)
    uuid_value = (
        (timestamp_ms << 80)
        | (UUIDV7_VERSION << 76)
        | (random_a << 64)
        | (UUID_RFC4122_VARIANT << 62)
        | random_b
    )
    return UUID(int=uuid_value)


def _generate_prefixed_uuid7(prefix: str) -> str:
    """生成带业务前缀的 UUIDv7 字符串。

    :param prefix: 业务 ID 前缀。
    :return: 带前缀的 UUIDv7 字符串。
    """

    return f"{prefix}{_uuid7()}"


def _build_detail(field: str, reason: str) -> ErrorDetailDto:
    """构建字段级错误明细。

    :param field: 发生错误的字段路径。
    :param reason: 错误原因。
    :return: 字段级错误明细 DTO。
    """

    return ErrorDetailDto(field=field, reason=reason)


def _is_valid_identity_value(
    value: str,
    settings: ApiIngressSettings,
) -> bool:
    """判断请求身份字段值是否满足入口格式约束。

    :param value: 需要判断的请求身份字段值。
    :param settings: API 接入组件配置。
    :return: 若字段值满足长度与字符约束，则返回 True。
    """

    if len(value) > settings.request_identity.max_id_length:
        return False
    return re.fullmatch(settings.request_identity.allowed_id_pattern, value) is not None


def _validate_identity_value(
    field: str,
    value: str | None,
    settings: ApiIngressSettings,
) -> list[ErrorDetailDto]:
    """校验单个请求身份字段格式。

    :param field: 字段路径。
    :param value: 需要校验的字段值。
    :param settings: API 接入组件配置。
    :return: 字段级错误明细列表；通过时返回空列表。
    """

    if value is None:
        return []
    details: list[ErrorDetailDto] = []
    if len(value) > settings.request_identity.max_id_length:
        details.append(_build_detail(field, "max_id_length_exceeded"))
    if re.fullmatch(settings.request_identity.allowed_id_pattern, value) is None:
        details.append(_build_detail(field, "invalid_id_format"))
    return details


def _select_valid_response_value(
    header_value: str | None,
    body_value: str | None,
    generated_value: str,
    settings: ApiIngressSettings,
) -> str:
    """选择错误响应中可安全回显的身份字段值。

    :param header_value: 请求头中传入的身份字段值。
    :param body_value: 请求体中传入的身份字段值。
    :param generated_value: 入口层生成的兜底身份字段值。
    :param settings: API 接入组件配置。
    :return: 已校验合法的身份字段值；无合法输入时返回生成值。
    """

    if header_value is not None and _is_valid_identity_value(header_value, settings):
        return header_value
    if body_value is not None and _is_valid_identity_value(body_value, settings):
        return body_value
    return generated_value


def _select_identity_value(
    header_value: str | None,
    body_value: str | None,
) -> tuple[str | None, IdentityValueSource | None]:
    """从请求头和请求体中选择身份字段值。

    :param header_value: 请求头中传入的身份字段值。
    :param body_value: 请求体中传入的身份字段值。
    :return: 选中的身份字段值及其来源；两者均缺失时值和来源均为 None。
    """

    if header_value is not None:
        return header_value, "header"
    if body_value is not None:
        return body_value, "body"
    return None, None


def _resolve_single_identity(
    field: str,
    header_name: str,
    header_value: str | None,
    body_value: str | None,
    prefix: str,
    settings: ApiIngressSettings,
) -> _SingleIdentityResolution:
    """解析或生成单个入口请求身份字段。

    :param field: 请求体中的字段名。
    :param header_name: 请求头中的字段名。
    :param header_value: 请求头中传入的身份字段值。
    :param body_value: 请求体中传入的身份字段值。
    :param prefix: 自动生成 ID 时使用的前缀。
    :param settings: API 接入组件配置。
    :return: 单个请求身份字段解析结果。
    """

    generated_value = _generate_prefixed_uuid7(prefix)
    effective_body_value = (
        body_value if settings.request_identity.allow_body_ids else None
    )
    details: list[ErrorDetailDto] = []

    if body_value is not None and not settings.request_identity.allow_body_ids:
        details.append(_build_detail(field, "body_identity_not_allowed"))

    details.extend(_validate_identity_value(header_name, header_value, settings))
    if settings.request_identity.allow_body_ids:
        details.extend(_validate_identity_value(field, body_value, settings))

    if (
        header_value is not None
        and effective_body_value is not None
        and header_value != effective_body_value
    ):
        details.append(_build_detail(field, "header_body_conflict"))

    selected_value, selected_source = _select_identity_value(
        header_value,
        effective_body_value,
    )
    response_value = _select_valid_response_value(
        header_value=header_value,
        body_value=effective_body_value,
        generated_value=generated_value,
        settings=settings,
    )

    if selected_value is not None and selected_source is not None:
        if _is_valid_identity_value(selected_value, settings):
            return _SingleIdentityResolution(
                value=selected_value,
                source=selected_source,
                response_value=response_value,
                details=details,
            )
        return _SingleIdentityResolution(
            value=response_value,
            source="generated",
            response_value=response_value,
            details=details,
        )

    if settings.request_identity.generate_when_missing:
        return _SingleIdentityResolution(
            value=generated_value,
            source="generated",
            response_value=generated_value,
            details=details,
        )

    details.append(_build_detail(field, "missing_required_identity"))
    return _SingleIdentityResolution(
        value=generated_value,
        source="generated",
        response_value=generated_value,
        details=details,
    )


def resolve_request_identity(
    request: Request,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> RequestIdentityResolution:
    """解析 API 接入请求的 request_id 与 trace_id。

    :param request: 当前 HTTP 请求对象。
    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 请求身份解析结果；失败时携带统一错误响应。
    """

    request_id_resolution = _resolve_single_identity(
        field="request_id",
        header_name=settings.request_identity.request_id_header,
        header_value=request.headers.get(settings.request_identity.request_id_header),
        body_value=turn_request.request_id,
        prefix=settings.request_identity.request_id_prefix,
        settings=settings,
    )
    trace_id_resolution = _resolve_single_identity(
        field="trace_id",
        header_name=settings.request_identity.trace_id_header,
        header_value=request.headers.get(settings.request_identity.trace_id_header),
        body_value=turn_request.trace_id,
        prefix=settings.request_identity.trace_id_prefix,
        settings=settings,
    )
    details = [
        *request_id_resolution.details,
        *trace_id_resolution.details,
    ]
    if details:
        error_response = build_api_ingress_error_response(
            settings=settings,
            code=IngressErrorCode.INVALID_REQUEST,
            request_id=request_id_resolution.response_value,
            trace_id=trace_id_resolution.response_value,
            public_message="invalid request",
            diagnostic_message="invalid request identity",
            details=details,
        )
        return RequestIdentityResolution(
            identity_context=None,
            failure=RequestIdentityResolutionFailure(
                status_code=400,
                error_response=error_response,
            ),
        )
    return RequestIdentityResolution(
        identity_context=RequestIdentityContext(
            request_id=request_id_resolution.value,
            trace_id=trace_id_resolution.value,
            request_id_source=request_id_resolution.source,
            trace_id_source=trace_id_resolution.source,
        ),
        failure=None,
    )


__all__: tuple[str, ...] = (
    "IdentityValueSource",
    "RequestIdentityContext",
    "RequestIdentityResolution",
    "RequestIdentityResolutionFailure",
    "resolve_request_identity",
)
