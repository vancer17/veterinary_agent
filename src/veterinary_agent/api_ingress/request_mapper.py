##################################################################################################
# 文件: src/veterinary_agent/api_ingress/request_mapper.py
# 作用: 将外部 API 请求一次性映射为 AgentApplicationService 可执行的应用命令。
# 边界: 只负责入口身份、响应模式、执行选项和字段转换；不访问存储、不执行领域判定。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.agent_application_service import (
    AgentTurnDiagnosticsDto,
    AgentTurnExecutionOptionsDto,
    AgentTurnPublishCapabilitiesDto,
    AgentTurnRequestCommandDto,
)
from veterinary_agent.api_ingress.dto import AgentTurnRequestDto
from veterinary_agent.api_ingress.enums import ApiRouteKind, ResponseMode
from veterinary_agent.api_ingress.identity import RequestIdentityContext
from veterinary_agent.config import ApiIngressSettings


def _resolve_response_mode(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> ResponseMode:
    """解析本轮请求最终使用的响应模式。

    :param turn_request: 已通过外部结构校验的一轮对话请求。
    :param settings: API 接入配置。
    :return: 归一化后的响应模式。
    """

    if turn_request.stream is True:
        return ResponseMode.STREAM
    if turn_request.stream is False:
        return ResponseMode.SYNC
    if turn_request.turn_options and turn_request.turn_options.response_mode:
        return turn_request.turn_options.response_mode
    return (
        ResponseMode.STREAM
        if settings.response_mode.default_stream
        else ResponseMode.SYNC
    )


def _build_execution_options(
    settings: ApiIngressSettings,
) -> AgentTurnExecutionOptionsDto:
    """从入口配置构建应用执行选项。

    :param settings: API 接入配置。
    :return: 应用层使用的执行选项。
    """

    orchestrator = settings.orchestrator
    sse = settings.sse
    return AgentTurnExecutionOptionsDto(
        orchestrator_target=orchestrator.target,
        connect_timeout_seconds=orchestrator.connect_timeout_seconds,
        request_timeout_seconds=orchestrator.request_timeout_seconds,
        stream_first_event_timeout_seconds=orchestrator.stream_first_event_timeout_seconds,
        stream_total_timeout_seconds=orchestrator.stream_total_timeout_seconds,
        heartbeat_enabled=sse.heartbeat_enabled,
        heartbeat_interval_seconds=sse.heartbeat_interval_seconds,
        stream_idle_timeout_seconds=sse.idle_timeout_seconds,
        max_stream_duration_seconds=sse.max_stream_duration_seconds,
        max_event_bytes=sse.max_event_bytes,
        client_cancel_notify_timeout_seconds=sse.client_cancel_notify_timeout_seconds,
    )


def _build_publish_capabilities(
    response_mode: ResponseMode,
) -> AgentTurnPublishCapabilitiesDto:
    """构建当前入口可承载的发布能力。

    :param response_mode: 当前请求归一化后的响应模式。
    :return: 当前请求对应的发布能力。
    """

    return AgentTurnPublishCapabilitiesDto(
        supports_sse_events=response_mode is ResponseMode.STREAM,
    )


def _build_diagnostics(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> AgentTurnDiagnosticsDto:
    """构建入口诊断摘要。

    :param turn_request: 外部一轮对话请求。
    :param settings: API 接入配置。
    :return: 当前请求的入口诊断摘要。
    """

    return AgentTurnDiagnosticsDto(
        service_name=settings.service_name,
        environment=settings.environment,
        config_version=settings.config_version,
        input_count=len(turn_request.input or []),
        attachment_count=len(turn_request.attachments or []),
    )


def map_agent_turn_request(
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
    route_kind: ApiRouteKind,
    identity_context: RequestIdentityContext,
) -> AgentTurnRequestCommandDto:
    """将外部请求映射为应用层单轮执行命令。

    :param turn_request: 已通过入口解析和后置校验的外部请求。
    :param settings: API 接入配置。
    :param route_kind: 当前入口路由类型。
    :param identity_context: 已解析的请求身份上下文。
    :return: 可传递给 AgentApplicationService 的应用命令。
    """

    response_mode = _resolve_response_mode(turn_request, settings)
    turn_options = turn_request.turn_options
    idempotency_key = (
        turn_options.idempotency_key
        if turn_options is not None and turn_options.idempotency_key
        else identity_context.request_id
    )
    return AgentTurnRequestCommandDto.model_validate(
        {
            "request_context": {
                "request_id": identity_context.request_id,
                "trace_id": identity_context.trace_id,
                "response_mode": response_mode.value,
                "received_at": datetime.now(UTC),
                "route_kind": route_kind.value,
            },
            "trusted_identity": {
                "user_id": turn_request.vet_context.user_id,
                "session_id": turn_request.vet_context.session_id,
                "pet_id": turn_request.vet_context.pet_id,
                "pet_info": turn_request.vet_context.pet_info,
            },
            "input": [
                item.model_dump(mode="json") for item in turn_request.input or []
            ],
            "attachments": [
                item.model_dump(mode="json") for item in turn_request.attachments or []
            ],
            "metadata": dict(turn_request.metadata or {}),
            "model_hint": turn_request.model,
            "turn_options": (
                turn_options.model_dump(mode="json")
                if turn_options is not None
                else None
            ),
            "idempotency_key": idempotency_key,
            "execution_options": _build_execution_options(settings).model_dump(
                mode="json"
            ),
            "publish_capabilities": _build_publish_capabilities(
                response_mode
            ).model_dump(mode="json"),
            "diagnostics": _build_diagnostics(turn_request, settings).model_dump(
                mode="json"
            ),
        }
    )


__all__: tuple[str, ...] = ("map_agent_turn_request",)
