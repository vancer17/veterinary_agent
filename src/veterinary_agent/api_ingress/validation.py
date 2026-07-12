##################################################################################################
# 文件: src/veterinary_agent/api_ingress/validation.py
# 作用: 定义 API 接入组件的 DTO 后置校验能力，补充 FastAPI / Pydantic 自动结构校验之外的入口契约校验。
# 边界: 仅处理 ApiIngress 请求 ID、配置化大小限制、附件引用一致性和入口字段约束，不执行编排、鉴权或兽医业务判断。
##################################################################################################

import json
from dataclasses import dataclass
from typing import Final

from fastapi import Request

from veterinary_agent.api_ingress.dto import (
    AgentTurnRequestDto,
    AttachmentRefDto,
    ErrorDetailDto,
    ErrorResponseDto,
    InputAttachmentContentDto,
    InputItemDto,
)
from veterinary_agent.agent_application_service import AgentTurnRequestCommandDto
from veterinary_agent.api_ingress.error_response import (
    build_api_ingress_error_response,
)
from veterinary_agent.api_ingress.enums import (
    ApiRouteKind,
    IngressErrorCode,
    ResponseMode,
)
from veterinary_agent.api_ingress.identity import RequestIdentityContext
from veterinary_agent.config import ApiIngressSettings

JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")


@dataclass(slots=True)
class ApiIngressValidationFailure:
    """API 接入 DTO 后置校验失败结果。"""

    status_code: int
    error_response: ErrorResponseDto


def _json_size_bytes(value: object | None) -> int:
    """计算 JSON 值的 UTF-8 序列化字节数。

    :param value: 需要计算大小的 JSON 兼容值。
    :return: JSON 序列化后的 UTF-8 字节数；值为空时返回 0。
    """

    if value is None:
        return 0
    serialized_value = json.dumps(
        value,
        ensure_ascii=False,
        separators=JSON_SEPARATORS,
    )
    return len(serialized_value.encode("utf-8"))


def _build_detail(field: str, reason: str) -> ErrorDetailDto:
    """构建字段级错误明细。

    :param field: 发生错误的字段路径。
    :param reason: 错误原因。
    :return: 字段级错误明细 DTO。
    """

    return ErrorDetailDto(field=field, reason=reason)


def _build_failure(
    settings: ApiIngressSettings,
    status_code: int,
    code: IngressErrorCode,
    message: str,
    request_id: str,
    trace_id: str,
    details: list[ErrorDetailDto],
) -> ApiIngressValidationFailure:
    """构建 API 接入 DTO 后置校验失败结果。

    :param settings: API 接入组件配置。
    :param status_code: HTTP 状态码。
    :param code: 入口层错误码。
    :param message: 面向研发的错误说明。
    :param request_id: 本次入口请求 ID。
    :param trace_id: 本次链路 ID。
    :param details: 字段级或依赖级错误明细。
    :return: API 接入 DTO 后置校验失败结果。
    """

    return ApiIngressValidationFailure(
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


async def _validate_body_size(
    request: Request,
    settings: ApiIngressSettings,
    request_id: str,
    trace_id: str,
) -> ApiIngressValidationFailure | None:
    """校验原始请求体大小。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :param request_id: 错误响应使用的请求 ID。
    :param trace_id: 错误响应使用的链路 ID。
    :return: 校验失败结果；通过时返回 None。
    """

    body = await request.body()
    if len(body) <= settings.request_limits.max_body_bytes:
        return None
    return _build_failure(
        settings=settings,
        status_code=413,
        code=IngressErrorCode.PAYLOAD_TOO_LARGE,
        message="request body is too large",
        request_id=request_id,
        trace_id=trace_id,
        details=[_build_detail("body", "max_body_bytes_exceeded")],
    )


def _validate_request_metadata_limits(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> list[ErrorDetailDto]:
    """校验请求顶层 metadata 大小限制。

    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 字段级错误明细列表；通过时返回空列表。
    """

    if (
        _json_size_bytes(turn_request.metadata)
        <= settings.request_limits.max_metadata_bytes
    ):
        return []
    return [_build_detail("metadata", "max_metadata_bytes_exceeded")]


def _iter_input_attachment_contents(
    input_items: list[InputItemDto] | None,
) -> list[tuple[int, int, InputAttachmentContentDto]]:
    """收集输入内容中的附件引用项。

    :param input_items: 输入项列表。
    :return: 由输入项索引、内容项索引和附件引用内容组成的列表。
    """

    if not input_items:
        return []
    attachment_contents: list[tuple[int, int, InputAttachmentContentDto]] = []
    for input_index, input_item in enumerate(input_items):
        for content_index, content in enumerate(input_item.content):
            if isinstance(content, InputAttachmentContentDto):
                attachment_contents.append((input_index, content_index, content))
    return attachment_contents


def _validate_input_limits(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> tuple[list[ErrorDetailDto], list[ErrorDetailDto]]:
    """校验输入内容数量和文本长度限制。

    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 由 payload 超限明细和普通格式错误明细组成的元组。
    """

    payload_details: list[ErrorDetailDto] = []
    invalid_details: list[ErrorDetailDto] = []
    if turn_request.attachments and not turn_request.input:
        if not settings.request_limits.allow_attachment_only_turn:
            invalid_details.append(
                _build_detail("input", "attachment_only_turn_not_allowed")
            )
        if not settings.request_limits.allow_empty_input_when_attachments_present:
            invalid_details.append(_build_detail("input", "empty_input_not_allowed"))
    if not turn_request.input:
        return payload_details, invalid_details
    if len(turn_request.input) > settings.request_limits.max_input_items:
        payload_details.append(_build_detail("input", "max_input_items_exceeded"))

    total_text_chars = 0
    for input_index, input_item in enumerate(turn_request.input):
        if (
            len(input_item.content)
            > settings.request_limits.max_content_items_per_message
        ):
            payload_details.append(
                _build_detail(
                    f"input.{input_index}.content",
                    "max_content_items_per_message_exceeded",
                )
            )
        for content_index, content in enumerate(input_item.content):
            text = getattr(content, "text", None)
            if not isinstance(text, str):
                continue
            total_text_chars += len(text)
            if len(text) > settings.request_limits.max_text_chars_per_item:
                payload_details.append(
                    _build_detail(
                        f"input.{input_index}.content.{content_index}.text",
                        "max_text_chars_per_item_exceeded",
                    )
                )
    if total_text_chars > settings.request_limits.max_total_text_chars:
        payload_details.append(_build_detail("input", "max_total_text_chars_exceeded"))
    return payload_details, invalid_details


def _attachment_as_json_map(attachment: AttachmentRefDto) -> dict[str, object]:
    """将附件引用 DTO 转换为 JSON 兼容字典。

    :param attachment: 附件引用 DTO。
    :return: 附件引用的 JSON 兼容字典。
    """

    return attachment.model_dump(mode="json", exclude_none=True)


def _validate_attachment_limits(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> tuple[list[ErrorDetailDto], list[ErrorDetailDto]]:
    """校验附件元信息限制。

    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 由 payload 超限明细和普通格式错误明细组成的元组。
    """

    payload_details: list[ErrorDetailDto] = []
    invalid_details: list[ErrorDetailDto] = []
    if not turn_request.attachments:
        return payload_details, invalid_details

    attachments = turn_request.attachments
    if len(attachments) > settings.attachment_limits.max_attachments:
        payload_details.append(_build_detail("attachments", "max_attachments_exceeded"))

    total_attachment_bytes = 0
    allowed_mime_types = set(settings.attachment_limits.allowed_mime_types)
    for index, attachment in enumerate(attachments):
        total_attachment_bytes += _json_size_bytes(_attachment_as_json_map(attachment))
        if (
            len(attachment.attachment_id)
            > settings.attachment_limits.max_attachment_id_length
        ):
            payload_details.append(
                _build_detail(
                    f"attachments.{index}.attachment_id",
                    "max_attachment_id_length_exceeded",
                )
            )
        if (
            len(attachment.storage_ref)
            > settings.attachment_limits.max_storage_ref_length
        ):
            payload_details.append(
                _build_detail(
                    f"attachments.{index}.storage_ref",
                    "max_storage_ref_length_exceeded",
                )
            )
        if len(attachment.purpose) > settings.attachment_limits.max_purpose_length:
            payload_details.append(
                _build_detail(
                    f"attachments.{index}.purpose",
                    "max_purpose_length_exceeded",
                )
            )
        if (
            not settings.attachment_limits.allow_unknown_mime_type
            and attachment.mime_type not in allowed_mime_types
        ):
            invalid_details.append(
                _build_detail(f"attachments.{index}.mime_type", "unsupported_mime_type")
            )
        if (
            _json_size_bytes(attachment.metadata)
            > settings.attachment_limits.max_attachment_metadata_bytes
        ):
            payload_details.append(
                _build_detail(
                    f"attachments.{index}.metadata",
                    "max_attachment_metadata_bytes_exceeded",
                )
            )

    if (
        total_attachment_bytes
        > settings.attachment_limits.max_total_attachment_metadata_bytes
    ):
        payload_details.append(
            _build_detail("attachments", "max_total_attachment_metadata_bytes_exceeded")
        )
    return payload_details, invalid_details


def _validate_attachment_references(
    turn_request: AgentTurnRequestDto,
) -> list[ErrorDetailDto]:
    """校验输入内容中的附件引用是否存在。

    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :return: 字段级错误明细列表；通过时返回空列表。
    """

    attachment_ids = {
        attachment.attachment_id for attachment in turn_request.attachments or []
    }
    details: list[ErrorDetailDto] = []
    for input_index, content_index, content in _iter_input_attachment_contents(
        turn_request.input
    ):
        if content.attachment_id not in attachment_ids:
            details.append(
                _build_detail(
                    f"input.{input_index}.content.{content_index}.attachment_id",
                    "attachment_not_found",
                )
            )
    return details


def _build_payload_limit_failure(
    settings: ApiIngressSettings,
    request_id: str,
    trace_id: str,
    details: list[ErrorDetailDto],
) -> ApiIngressValidationFailure | None:
    """根据 payload 超限明细构建失败结果。

    :param settings: API 接入组件配置。
    :param request_id: 错误响应使用的请求 ID。
    :param trace_id: 错误响应使用的链路 ID。
    :param details: 字段级错误明细列表。
    :return: 校验失败结果；明细为空时返回 None。
    """

    if not details:
        return None
    return _build_failure(
        settings=settings,
        status_code=413,
        code=IngressErrorCode.PAYLOAD_TOO_LARGE,
        message="request payload is too large",
        request_id=request_id,
        trace_id=trace_id,
        details=details,
    )


def _build_invalid_request_failure(
    settings: ApiIngressSettings,
    request_id: str,
    trace_id: str,
    details: list[ErrorDetailDto],
) -> ApiIngressValidationFailure | None:
    """根据普通格式错误明细构建失败结果。

    :param settings: API 接入组件配置。
    :param request_id: 错误响应使用的请求 ID。
    :param trace_id: 错误响应使用的链路 ID。
    :param details: 字段级错误明细列表。
    :return: 校验失败结果；明细为空时返回 None。
    """

    if not details:
        return None
    return _build_failure(
        settings=settings,
        status_code=400,
        code=IngressErrorCode.INVALID_REQUEST,
        message="invalid request",
        request_id=request_id,
        trace_id=trace_id,
        details=details,
    )


def _build_response_mode_availability_failure(
    command: AgentTurnRequestCommandDto,
    settings: ApiIngressSettings,
    reason: str,
) -> ApiIngressValidationFailure:
    """构建响应模式不可用的校验失败结果。

    :param command: 已完成入口映射的应用层请求命令。
    :param settings: API 接入组件配置。
    :param reason: 响应模式不可用的原因。
    :return: 响应模式不可用的校验失败结果。
    """

    request_context = command.request_context
    return _build_failure(
        settings=settings,
        status_code=400,
        code=IngressErrorCode.INVALID_REQUEST,
        message="response mode is not allowed",
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        details=[_build_detail("response_mode", reason)],
    )


def validate_response_mode_availability(
    command: AgentTurnRequestCommandDto,
    settings: ApiIngressSettings,
) -> ApiIngressValidationFailure | None:
    """校验归一化后的响应模式是否被当前入口配置允许。

    :param command: 已完成入口映射的应用层请求命令。
    :param settings: API 接入组件配置。
    :return: 校验失败结果；当前响应模式被允许时返回 None。
    """

    response_mode = command.request_context.response_mode
    if (
        response_mode == ResponseMode.SYNC.value
        and not settings.response_mode.allow_sync
    ):
        return _build_response_mode_availability_failure(
            command=command,
            settings=settings,
            reason="sync_not_allowed",
        )
    if (
        response_mode == ResponseMode.STREAM.value
        and not settings.response_mode.allow_stream
    ):
        return _build_response_mode_availability_failure(
            command=command,
            settings=settings,
            reason="stream_not_allowed",
        )
    return None


def validate_sync_response_mode_availability(
    command: AgentTurnRequestCommandDto,
    settings: ApiIngressSettings,
) -> ApiIngressValidationFailure | None:
    """校验同步响应模式是否被当前入口配置允许。

    :param command: 已完成入口映射的应用层请求命令。
    :param settings: API 接入组件配置。
    :return: 校验失败结果；同步模式被允许或当前请求非同步模式时返回 None。
    """

    if command.request_context.response_mode != ResponseMode.SYNC.value:
        return None
    return validate_response_mode_availability(
        command=command,
        settings=settings,
    )


async def validate_agent_turn_request(
    request: Request,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
    route_kind: ApiRouteKind,
    identity_context: RequestIdentityContext,
) -> ApiIngressValidationFailure | None:
    """执行 API 接入一轮对话请求的 DTO 后置校验。

    :param request: 当前 HTTP 请求对象。
    :param turn_request: 已通过 Pydantic 结构校验的外部请求 DTO。
    :param settings: API 接入组件配置。
    :param route_kind: 当前入口路由类型；当前阶段仅用于保留兼容入口校验扩展点。
    :param identity_context: 已解析的入口请求身份上下文。
    :return: 校验失败结果；全部通过时返回 None。
    """

    del route_kind

    request_id = identity_context.request_id
    trace_id = identity_context.trace_id

    body_size_failure = await _validate_body_size(
        request=request,
        settings=settings,
        request_id=request_id,
        trace_id=trace_id,
    )
    if body_size_failure is not None:
        return body_size_failure

    payload_details: list[ErrorDetailDto] = []
    invalid_details: list[ErrorDetailDto] = []
    payload_details.extend(_validate_request_metadata_limits(turn_request, settings))
    input_payload_details, input_invalid_details = _validate_input_limits(
        turn_request,
        settings,
    )
    payload_details.extend(input_payload_details)
    invalid_details.extend(input_invalid_details)
    attachment_payload_details, attachment_invalid_details = (
        _validate_attachment_limits(
            turn_request,
            settings,
        )
    )
    payload_details.extend(attachment_payload_details)
    invalid_details.extend(attachment_invalid_details)
    invalid_details.extend(_validate_attachment_references(turn_request))

    payload_failure = _build_payload_limit_failure(
        settings, request_id, trace_id, payload_details
    )
    if payload_failure is not None:
        return payload_failure
    return _build_invalid_request_failure(
        settings,
        request_id,
        trace_id,
        invalid_details,
    )


__all__: tuple[str, ...] = (
    "ApiIngressValidationFailure",
    "validate_agent_turn_request",
    "validate_response_mode_availability",
    "validate_sync_response_mode_availability",
)
