##################################################################################################
# 文件: src/veterinary_agent/api_ingress/error_response.py
# 作用: 定义 API 接入组件内部错误响应策略，统一消费 error_response.* 配置并构建对外错误响应。
# 边界: 仅处理错误响应的展示、裁剪与脱敏；不执行 DTO 校验、编排调用、限流判定或领域安全审查。
##################################################################################################

from typing import Final, Literal, TypeAlias

from fastapi.responses import JSONResponse

from veterinary_agent.api_ingress.dto import ErrorDetailDto, ErrorResponseDto
from veterinary_agent.api_ingress.enums import IngressErrorCode
from veterinary_agent.config import ApiIngressSettings

ApiIngressErrorResponseSource: TypeAlias = Literal["client", "dependency", "internal"]

CLIENT_ERROR_SOURCE: Final[ApiIngressErrorResponseSource] = "client"
DEPENDENCY_ERROR_SOURCE: Final[ApiIngressErrorResponseSource] = "dependency"
INTERNAL_ERROR_SOURCE: Final[ApiIngressErrorResponseSource] = "internal"


def _resolve_message(
    settings: ApiIngressSettings,
    public_message: str | None,
    diagnostic_message: str | None,
) -> str:
    """解析对外错误消息。

    :param settings: API 接入组件配置。
    :param public_message: 默认对外展示的稳定错误消息。
    :param diagnostic_message: 允许详细诊断时可展示的错误消息。
    :return: 已按 error_response.detailed_message_enabled 处理后的错误消息。
    """

    if settings.error_response.detailed_message_enabled and diagnostic_message:
        return diagnostic_message
    if public_message:
        return public_message
    return settings.error_response.default_message


def _should_hide_details(
    settings: ApiIngressSettings,
    source: ApiIngressErrorResponseSource,
) -> bool:
    """判断是否需要隐藏内部错误明细。

    :param settings: API 接入组件配置。
    :param source: 错误来源分类。
    :return: 当前错误明细是否应按内部依赖或内部异常策略隐藏。
    """

    return settings.error_response.hide_internal_dependency_details and source in {
        DEPENDENCY_ERROR_SOURCE,
        INTERNAL_ERROR_SOURCE,
    }


def _hidden_detail_for_source(
    source: ApiIngressErrorResponseSource,
) -> ErrorDetailDto:
    """构建内部错误明细隐藏后的占位明细。

    :param source: 错误来源分类。
    :return: 脱敏后的错误明细占位 DTO。
    """

    if source == INTERNAL_ERROR_SOURCE:
        return ErrorDetailDto(reason="internal_error_details_hidden")
    return ErrorDetailDto(reason="internal_dependency_details_hidden")


def _sanitize_details(
    settings: ApiIngressSettings,
    details: list[ErrorDetailDto] | None,
    source: ApiIngressErrorResponseSource,
) -> list[ErrorDetailDto] | None:
    """按配置清理错误明细。

    :param settings: API 接入组件配置。
    :param details: 原始错误明细列表。
    :param source: 错误来源分类。
    :return: 已按 include_details、max_details 与隐藏策略处理后的错误明细。
    """

    if not settings.error_response.include_details:
        return None

    if _should_hide_details(settings=settings, source=source):
        sanitized_details = [_hidden_detail_for_source(source)]
    else:
        sanitized_details = list(details or [])

    return sanitized_details[: settings.error_response.max_details]


def build_api_ingress_error_response(
    *,
    settings: ApiIngressSettings,
    code: IngressErrorCode,
    request_id: str,
    trace_id: str,
    public_message: str | None = None,
    diagnostic_message: str | None = None,
    details: list[ErrorDetailDto] | None = None,
    source: ApiIngressErrorResponseSource = CLIENT_ERROR_SOURCE,
) -> ErrorResponseDto:
    """构建符合 API 接入组件错误响应策略的 DTO。

    :param settings: API 接入组件配置。
    :param code: 入口层错误码。
    :param request_id: 本次入口请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param public_message: 默认对外展示的稳定错误消息。
    :param diagnostic_message: 允许详细诊断时可展示的错误消息。
    :param details: 原始字段级或依赖级错误明细。
    :param source: 错误来源分类，用于决定是否隐藏内部明细。
    :return: 已完成裁剪与脱敏的统一错误响应 DTO。
    """

    return ErrorResponseDto(
        code=code,
        message=_resolve_message(
            settings=settings,
            public_message=public_message,
            diagnostic_message=diagnostic_message,
        ),
        request_id=request_id,
        trace_id=trace_id,
        details=_sanitize_details(
            settings=settings,
            details=details,
            source=source,
        ),
    )


def build_api_ingress_json_error_response(
    *,
    settings: ApiIngressSettings,
    status_code: int,
    code: IngressErrorCode,
    request_id: str,
    trace_id: str,
    public_message: str | None = None,
    diagnostic_message: str | None = None,
    details: list[ErrorDetailDto] | None = None,
    source: ApiIngressErrorResponseSource = CLIENT_ERROR_SOURCE,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """构建符合 API 接入组件错误响应策略的 JSON 响应。

    :param settings: API 接入组件配置。
    :param status_code: HTTP 状态码。
    :param code: 入口层错误码。
    :param request_id: 本次入口请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param public_message: 默认对外展示的稳定错误消息。
    :param diagnostic_message: 允许详细诊断时可展示的错误消息。
    :param details: 原始字段级或依赖级错误明细。
    :param source: 错误来源分类，用于决定是否隐藏内部明细。
    :param headers: 需要附加到 HTTP 响应上的 Header。
    :return: 已完成裁剪与脱敏的 JSON 错误响应。
    """

    error_response = build_api_ingress_error_response(
        settings=settings,
        code=code,
        request_id=request_id,
        trace_id=trace_id,
        public_message=public_message,
        diagnostic_message=diagnostic_message,
        details=details,
        source=source,
    )
    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json"),
        headers=headers,
    )


__all__: tuple[str, ...] = (
    "CLIENT_ERROR_SOURCE",
    "DEPENDENCY_ERROR_SOURCE",
    "INTERNAL_ERROR_SOURCE",
    "ApiIngressErrorResponseSource",
    "build_api_ingress_error_response",
    "build_api_ingress_json_error_response",
)
