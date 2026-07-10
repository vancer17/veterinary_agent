##################################################################################################
# 文件: src/veterinary_agent/api_ingress/builder.py
# 作用: 定义 AgentTurnRequest Builder，将 ApiIngress 内部归一化请求转换为编排层可消费的请求命令。
# 边界: 仅执行字段映射、幂等键补齐、执行选项与发布能力封装；不调用编排层、不访问存储、不执行兽医业务判断。
##################################################################################################

from veterinary_agent.agent_application_service import (
    AgentTurnDiagnosticsDto,
    AgentTurnExecutionOptionsDto,
    AgentTurnPublishCapabilitiesDto,
    AgentTurnRequestCommandDto,
)

from veterinary_agent.api_ingress.dto import (
    AgentTurnInternalRequestDto,
)
from veterinary_agent.api_ingress.enums import ResponseMode
from veterinary_agent.config import ApiIngressSettings


def _resolve_idempotency_key(
    normalized_request: AgentTurnInternalRequestDto,
) -> str:
    """解析传递给下游的幂等键。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :return: 下游可使用的幂等键；未显式传入时回退为 request_id。
    """

    turn_options = normalized_request.turn_options
    if turn_options and turn_options.idempotency_key:
        return turn_options.idempotency_key
    return normalized_request.request_context.request_id


def _build_execution_options(
    settings: ApiIngressSettings,
) -> AgentTurnExecutionOptionsDto:
    """构建编排执行选项。

    :param settings: API 接入组件配置。
    :return: 编排层可消费的一轮执行选项 DTO。
    """

    return AgentTurnExecutionOptionsDto(
        orchestrator_target=settings.orchestrator.target,
        connect_timeout_seconds=settings.orchestrator.connect_timeout_seconds,
        request_timeout_seconds=settings.orchestrator.request_timeout_seconds,
        stream_first_event_timeout_seconds=(
            settings.orchestrator.stream_first_event_timeout_seconds
        ),
        stream_total_timeout_seconds=settings.orchestrator.stream_total_timeout_seconds,
        heartbeat_enabled=settings.sse.heartbeat_enabled,
        heartbeat_interval_seconds=settings.sse.heartbeat_interval_seconds,
        stream_idle_timeout_seconds=settings.sse.idle_timeout_seconds,
        max_stream_duration_seconds=settings.sse.max_stream_duration_seconds,
        max_event_bytes=settings.sse.max_event_bytes,
        client_cancel_notify_timeout_seconds=(
            settings.sse.client_cancel_notify_timeout_seconds
        ),
    )


def _build_publish_capabilities(
    normalized_request: AgentTurnInternalRequestDto,
) -> AgentTurnPublishCapabilitiesDto:
    """构建当前入口可承载的发布能力。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :return: 当前请求对应的发布能力 DTO。
    """

    return AgentTurnPublishCapabilitiesDto(
        supports_sse_events=(
            normalized_request.request_context.response_mode is ResponseMode.STREAM
        ),
    )


def _build_diagnostics(
    normalized_request: AgentTurnInternalRequestDto,
    settings: ApiIngressSettings,
) -> AgentTurnDiagnosticsDto:
    """构建入口诊断摘要。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 当前一轮请求的入口诊断摘要 DTO。
    """

    return AgentTurnDiagnosticsDto(
        service_name=settings.service_name,
        environment=settings.environment,
        config_version=settings.config_version,
        input_count=len(normalized_request.input),
        attachment_count=len(normalized_request.attachments),
    )


def build_agent_turn_request(
    normalized_request: AgentTurnInternalRequestDto,
    settings: ApiIngressSettings,
) -> AgentTurnRequestCommandDto:
    """构建面向编排层的一轮 Agent 请求命令。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :param settings: API 接入组件配置。
    :return: 可传递给 AgentApplicationService 的请求命令 DTO。
    """

    return AgentTurnRequestCommandDto.model_validate(
        {
            "request_context": normalized_request.request_context.model_dump(
                mode="json"
            ),
            "trusted_identity": normalized_request.trusted_identity.model_dump(
                mode="json"
            ),
            "input": [
                item.model_dump(mode="json") for item in normalized_request.input
            ],
            "attachments": [
                attachment.model_dump(mode="json")
                for attachment in normalized_request.attachments
            ],
            "metadata": dict(normalized_request.metadata),
            "model_hint": normalized_request.model,
            "turn_options": (
                normalized_request.turn_options.model_dump(mode="json")
                if normalized_request.turn_options is not None
                else None
            ),
            "idempotency_key": _resolve_idempotency_key(normalized_request),
            "execution_options": _build_execution_options(settings).model_dump(
                mode="json"
            ),
            "publish_capabilities": _build_publish_capabilities(
                normalized_request
            ).model_dump(mode="json"),
            "diagnostics": _build_diagnostics(
                normalized_request,
                settings,
            ).model_dump(mode="json"),
        }
    )


__all__: tuple[str, ...] = (
    "AgentTurnDiagnosticsDto",
    "AgentTurnExecutionOptionsDto",
    "AgentTurnPublishCapabilitiesDto",
    "AgentTurnRequestCommandDto",
    "build_agent_turn_request",
)
