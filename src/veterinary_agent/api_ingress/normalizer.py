##################################################################################################
# 文件: src/veterinary_agent/api_ingress/normalizer.py
# 作用: 定义 API 接入组件的 Ingress Normalizer，将外部请求 DTO 归一化为编排层可消费的内部请求 DTO。
# 边界: 仅使用已解析 ID、执行响应模式归一化、上下文拆分和空值标准化，不调用编排层、不访问存储、不执行兽医业务判断。
##################################################################################################

from datetime import UTC, datetime

from fastapi import Request

from veterinary_agent.api_ingress.dto import (
    AgentTurnInternalRequestDto,
    AgentTurnRequestDto,
    RequestContextDto,
    TrustedIdentityDto,
)
from veterinary_agent.api_ingress.enums import ApiRouteKind, ResponseMode
from veterinary_agent.api_ingress.identity import RequestIdentityContext
from veterinary_agent.config import ApiIngressSettings


def _resolve_response_mode(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> ResponseMode:
    """解析内部响应模式。

    :param turn_request: 已通过校验的外部一轮对话请求 DTO。
    :param settings: API 接入组件配置。
    :return: 归一化后的响应模式。
    """

    if turn_request.stream is True:
        return ResponseMode.STREAM
    if turn_request.stream is False:
        return ResponseMode.SYNC
    if turn_request.turn_options and turn_request.turn_options.response_mode:
        return turn_request.turn_options.response_mode
    if settings.response_mode.default_stream:
        return ResponseMode.STREAM
    return ResponseMode.SYNC


def _build_request_context(
    identity_context: RequestIdentityContext,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
    route_kind: ApiRouteKind,
) -> RequestContextDto:
    """构建内部请求上下文 DTO。

    :param identity_context: 已解析的入口请求身份上下文。
    :param turn_request: 已通过校验的外部一轮对话请求 DTO。
    :param settings: API 接入组件配置。
    :param route_kind: 当前入口路由类型。
    :return: 内部请求上下文 DTO。
    """

    return RequestContextDto(
        request_id=identity_context.request_id,
        trace_id=identity_context.trace_id,
        response_mode=_resolve_response_mode(turn_request, settings),
        received_at=datetime.now(UTC),
        route_kind=route_kind,
    )


def _build_trusted_identity(turn_request: AgentTurnRequestDto) -> TrustedIdentityDto:
    """构建上游可信身份上下文 DTO。

    :param turn_request: 已通过校验的外部一轮对话请求 DTO。
    :return: 上游可信身份上下文 DTO。
    """

    vet_context = turn_request.vet_context
    return TrustedIdentityDto(
        user_id=vet_context.user_id,
        session_id=vet_context.session_id,
        pet_id=vet_context.pet_id,
        pet_info=vet_context.pet_info,
    )


def normalize_agent_turn_request(
    request: Request,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
    route_kind: ApiRouteKind,
    identity_context: RequestIdentityContext,
) -> AgentTurnInternalRequestDto:
    """将外部一轮对话请求归一化为内部编排请求 DTO。

    :param request: 当前 HTTP 请求对象。
    :param turn_request: 已通过校验的外部一轮对话请求 DTO。
    :param settings: API 接入组件配置。
    :param route_kind: 当前入口路由类型。
    :param identity_context: 已解析的入口请求身份上下文。
    :return: 供后续 Builder 或编排层消费的内部归一化请求 DTO。
    """

    del request

    return AgentTurnInternalRequestDto(
        request_context=_build_request_context(
            identity_context=identity_context,
            turn_request=turn_request,
            settings=settings,
            route_kind=route_kind,
        ),
        trusted_identity=_build_trusted_identity(turn_request),
        input=list(turn_request.input or []),
        attachments=list(turn_request.attachments or []),
        metadata=dict(turn_request.metadata or {}),
        model=turn_request.model,
        turn_options=turn_request.turn_options,
    )


__all__: tuple[str, ...] = ("normalize_agent_turn_request",)
