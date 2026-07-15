##################################################################################################
# 文件: src/veterinary_agent/api_ingress/readiness.py
# 作用: 定义 API 接入组件就绪检查能力，根据 readiness 配置判断当前实例是否可接收正式流量。
# 边界: 仅检查 ApiIngress 运行配置与当前已装配依赖状态；领域外依赖未实现时使用 TODO 空壳占位。
##################################################################################################

import re
from dataclasses import dataclass

from veterinary_agent.api_ingress.dto import ErrorDetailDto
from veterinary_agent.config import ApiIngressSettings


@dataclass(slots=True)
class ApiIngressReadinessResult:
    """API 接入组件就绪检查结果。"""

    ready: bool
    details: list[ErrorDetailDto]


def _build_detail(field: str, reason: str) -> ErrorDetailDto:
    """构建就绪检查明细。

    :param field: 发生就绪检查问题的字段或依赖名称。
    :param reason: 就绪检查问题原因。
    :return: 就绪检查明细 DTO。
    """

    return ErrorDetailDto(field=field, reason=reason)


def _check_runtime_config(settings: ApiIngressSettings) -> list[ErrorDetailDto]:
    """检查 API 接入组件运行配置是否可用。

    :param settings: 已加载的 API 接入组件配置。
    :return: 就绪检查失败明细列表；通过时返回空列表。
    """

    details: list[ErrorDetailDto] = []
    if not settings.enabled:
        details.append(_build_detail("runtime_config.enabled", "disabled"))
    if not settings.service_name:
        details.append(_build_detail("runtime_config.service_name", "empty"))
    if not settings.environment:
        details.append(_build_detail("runtime_config.environment", "empty"))
    if not settings.config_version:
        details.append(_build_detail("runtime_config.config_version", "empty"))
    return details


def _check_identity_parameters(settings: ApiIngressSettings) -> list[ErrorDetailDto]:
    """检查请求身份相关必要参数。

    :param settings: 已加载的 API 接入组件配置。
    :return: 就绪检查失败明细列表；通过时返回空列表。
    """

    details: list[ErrorDetailDto] = []
    if not settings.request_identity.request_id_header:
        details.append(_build_detail("request_identity.request_id_header", "empty"))
    if not settings.request_identity.trace_id_header:
        details.append(_build_detail("request_identity.trace_id_header", "empty"))
    try:
        re.compile(settings.request_identity.allowed_id_pattern)
    except re.error:
        details.append(_build_detail("request_identity.allowed_id_pattern", "invalid"))
    return details


def _check_response_mode_parameters(
    settings: ApiIngressSettings,
) -> list[ErrorDetailDto]:
    """检查响应模式相关必要参数。

    :param settings: 已加载的 API 接入组件配置。
    :return: 就绪检查失败明细列表；通过时返回空列表。
    """

    details: list[ErrorDetailDto] = []
    if (
        not settings.response_mode.allow_sync
        and not settings.response_mode.allow_stream
    ):
        details.append(_build_detail("response_mode", "no_allowed_response_mode"))
    if (
        settings.response_mode.default_stream
        and not settings.response_mode.allow_stream
    ):
        details.append(_build_detail("response_mode.default_stream", "stream_disabled"))
    if (
        not settings.response_mode.default_stream
        and not settings.response_mode.allow_sync
    ):
        details.append(_build_detail("response_mode.default_stream", "sync_disabled"))
    return details


def _check_sse_parameters(settings: ApiIngressSettings) -> list[ErrorDetailDto]:
    """检查 SSE 相关必要参数。

    :param settings: 已加载的 API 接入组件配置。
    :return: 就绪检查失败明细列表；通过时返回空列表。
    """

    if not settings.response_mode.allow_stream:
        return []

    details: list[ErrorDetailDto] = []
    if (
        settings.sse.heartbeat_enabled
        and settings.sse.heartbeat_interval_seconds >= settings.sse.idle_timeout_seconds
    ):
        details.append(
            _build_detail(
                "sse.heartbeat_interval_seconds", "not_less_than_idle_timeout"
            )
        )
    if (
        settings.sse.first_event_timeout_seconds
        > settings.sse.max_stream_duration_seconds
    ):
        details.append(
            _build_detail(
                "sse.first_event_timeout_seconds", "greater_than_max_stream_duration"
            )
        )
    if settings.sse.max_event_bytes <= 0:
        details.append(_build_detail("sse.max_event_bytes", "not_positive"))
    return details


def _check_required_parameters(settings: ApiIngressSettings) -> list[ErrorDetailDto]:
    """检查 API 接入组件运行期必要参数。

    :param settings: 已加载的 API 接入组件配置。
    :return: 就绪检查失败明细列表；通过时返回空列表。
    """

    details: list[ErrorDetailDto] = []
    details.extend(_check_identity_parameters(settings))
    details.extend(_check_response_mode_parameters(settings))
    details.extend(_check_sse_parameters(settings))
    return details


def _check_orchestrator_dependency(
    settings: ApiIngressSettings,
    agent_application_service_ready: bool,
) -> list[ErrorDetailDto]:
    """检查 Agent 应用编排服务状态。

    :param settings: 已加载的 API 接入组件配置。
    :param agent_application_service_ready: AgentApplicationService 是否已装配且就绪。
    :return: 就绪时返回空列表；不可用时返回依赖明细。
    """

    if not settings.readiness.check_orchestrator:
        return []
    if agent_application_service_ready:
        return []
    return [_build_detail("agent_application_service", "unavailable")]


def _check_observability_dependency(
    settings: ApiIngressSettings,
    observability_ready: bool,
) -> list[ErrorDetailDto]:
    """检查可观测性依赖占位状态。

    :param settings: 已加载的 API 接入组件配置。
    :param observability_ready: Observability provider 是否已装配且就绪。
    :return: 就绪检查失败明细列表；就绪或允许降级时返回空列表。
    """

    if observability_ready:
        return []
    if settings.readiness.allow_degraded_observability:
        return []
    return [_build_detail("observability", "unavailable")]


def check_api_ingress_readiness(
    settings: ApiIngressSettings,
    app_ready: bool,
    runtime_config_ready: bool = True,
    checkpoint_store_runtime_config_ready: bool = True,
    checkpoint_store_ready: bool = True,
    conversation_store_runtime_config_ready: bool = True,
    llm_gateway_runtime_config_ready: bool = True,
    llm_gateway_required: bool = False,
    llm_gateway_ready: bool = True,
    pet_session_policy_ready: bool = True,
    agent_application_service_ready: bool = False,
    observability_ready: bool = True,
) -> ApiIngressReadinessResult:
    """检查 API 接入组件是否就绪。

    :param settings: 已加载的 API 接入组件配置。
    :param app_ready: ASGI 应用框架级就绪标记。
    :param runtime_config_ready: RuntimeConfig provider 与当前配置快照是否已装配。
    :param checkpoint_store_runtime_config_ready: CheckpointStore RuntimeConfig 是否已装配。
    :param checkpoint_store_ready: CheckpointStore 控制面存储是否已装配且可供 GraphRuntime 使用。
    :param conversation_store_runtime_config_ready: ConversationStore RuntimeConfig 是否已装配。
    :param llm_gateway_runtime_config_ready: LlmGateway RuntimeConfig 是否已装配。
    :param llm_gateway_required: 当前部署是否要求 LlmGateway 具备真实模型调用能力。
    :param llm_gateway_ready: LlmGateway 是否已装配且具备执行条件。
    :param pet_session_policy_ready: PetSessionPolicy 是否已装配且具备执行条件。
    :param agent_application_service_ready: AgentApplicationService 是否已装配且具备执行条件。
    :param observability_ready: Observability provider 是否已装配且就绪。
    :return: API 接入组件就绪检查结果。
    """

    details: list[ErrorDetailDto] = []
    if not app_ready:
        details.append(_build_detail("app.state.ready", "false"))
    if settings.readiness.check_runtime_config:
        details.extend(_check_runtime_config(settings))
        if not runtime_config_ready:
            details.append(
                _build_detail(
                    "runtime_config.snapshot",
                    "missing",
                )
            )
        if not checkpoint_store_runtime_config_ready:
            details.append(
                _build_detail(
                    "checkpoint_store.runtime_config",
                    "missing",
                )
            )
        if settings.readiness.check_orchestrator and not checkpoint_store_ready:
            details.append(
                _build_detail(
                    "checkpoint_store",
                    "unavailable",
                )
            )
        if not conversation_store_runtime_config_ready:
            details.append(
                _build_detail(
                    "conversation_store.runtime_config",
                    "missing",
                )
            )
        if not llm_gateway_runtime_config_ready:
            details.append(
                _build_detail(
                    "llm_gateway.runtime_config",
                    "missing",
                )
            )
    if llm_gateway_required and not llm_gateway_ready:
        details.append(
            _build_detail(
                "llm_gateway",
                "unavailable",
            )
        )
    if not pet_session_policy_ready:
        details.append(
            _build_detail(
                "pet_session_policy",
                "unavailable",
            )
        )
    if settings.readiness.validate_required_parameters:
        details.extend(_check_required_parameters(settings))
    details.extend(
        _check_orchestrator_dependency(
            settings,
            agent_application_service_ready,
        )
    )
    details.extend(_check_observability_dependency(settings, observability_ready))
    return ApiIngressReadinessResult(ready=not details, details=details)


__all__: tuple[str, ...] = (
    "ApiIngressReadinessResult",
    "check_api_ingress_readiness",
)
