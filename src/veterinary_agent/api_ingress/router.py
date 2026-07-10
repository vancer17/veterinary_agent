##################################################################################################
# 文件: src/veterinary_agent/api_ingress/router.py
# 作用: 定义 API 接入组件的 FastAPI Router，注册对话入口并将请求委派给后续 ApiIngress 处理链路。
# 边界: 当前接入 DTO Validation 与 Ingress Normalizer；AgentTurnRequest Builder 与编排层调用以后续 TODO 依赖替换接入。
##################################################################################################

from collections.abc import Callable, Coroutine
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from veterinary_agent.api_ingress.builder import (
    AgentTurnRequestCommandDto,
    build_agent_turn_request,
)
from veterinary_agent.api_ingress.concurrency import ApiIngressConcurrencyGate
from veterinary_agent.api_ingress.dto import (
    AgentTurnInternalRequestDto,
    ErrorDetailDto,
)
from veterinary_agent.api_ingress.error_response import (
    CLIENT_ERROR_SOURCE,
    DEPENDENCY_ERROR_SOURCE,
    INTERNAL_ERROR_SOURCE,
    ApiIngressErrorResponseSource,
    build_api_ingress_json_error_response,
)
from veterinary_agent.api_ingress.enums import ApiRouteKind, IngressErrorCode
from veterinary_agent.api_ingress.identity import (
    RequestIdentityResolutionFailure,
    resolve_request_identity,
)
from veterinary_agent.api_ingress.normalizer import normalize_agent_turn_request
from veterinary_agent.api_ingress.rate_limit import (
    ApiIngressRateLimitDecision,
    ApiIngressRateLimiter,
)
from veterinary_agent.api_ingress.request_parser import (
    ApiIngressRequestParseFailure,
    parse_agent_turn_request,
)
from veterinary_agent.api_ingress.validation import (
    ApiIngressValidationFailure,
    validate_agent_turn_request,
    validate_response_mode_availability,
)
from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.observability import ObservabilityProvider
from veterinary_agent.pet_session_policy import (
    PetSessionContextDto,
    PetSessionPolicy,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionRequestContextDto,
)

APP_STATE_KEY: Final[str] = "veterinary_agent_state"
REQUEST_ID_FALLBACK: Final[str] = "req_unavailable"
TRACE_ID_FALLBACK: Final[str] = "trace_unavailable"


def _get_api_ingress_settings(request: Request) -> ApiIngressSettings:
    """从 FastAPI 应用状态读取 API 接入组件配置。

    :param request: 当前 HTTP 请求对象。
    :return: 已加载并通过校验的 API 接入组件配置。
    :raises RuntimeError: 当应用状态或配置尚未初始化时抛出。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    settings = getattr(app_state, "settings", None)
    if not isinstance(settings, ApiIngressSettings):
        raise RuntimeError("API 接入组件配置尚未初始化")
    return settings


def _get_orchestrator_concurrency_gate(
    request: Request,
) -> ApiIngressConcurrencyGate:
    """从 FastAPI 应用状态读取编排入口并发闸门。

    :param request: 当前 HTTP 请求对象。
    :return: 已按配置初始化的编排入口并发闸门。
    :raises RuntimeError: 当应用状态或并发闸门尚未初始化时抛出。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    concurrency_gate = getattr(app_state, "orchestrator_concurrency_gate", None)
    if not isinstance(concurrency_gate, ApiIngressConcurrencyGate):
        raise RuntimeError("API 接入组件编排并发闸门尚未初始化")
    return concurrency_gate


def _get_api_ingress_rate_limiter(request: Request) -> ApiIngressRateLimiter:
    """从 FastAPI 应用状态读取 API 接入组件限流器。

    :param request: 当前 HTTP 请求对象。
    :return: 已按配置初始化的 API 接入组件限流器。
    :raises RuntimeError: 当应用状态或限流器尚未初始化时抛出。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    rate_limiter = getattr(app_state, "rate_limiter", None)
    if not isinstance(rate_limiter, ApiIngressRateLimiter):
        raise RuntimeError("API 接入组件限流器尚未初始化")
    return rate_limiter


def _get_observability_provider(request: Request) -> ObservabilityProvider | None:
    """从 FastAPI 应用状态读取 Observability provider。

    :param request: 当前 HTTP 请求对象。
    :return: 已装配且就绪的 Observability provider；未装配或未就绪时返回 None。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    provider = getattr(app_state, "observability_provider", None)
    if not isinstance(provider, ObservabilityProvider) or not provider.is_ready():
        return None
    return provider


def _get_pet_session_policy(request: Request) -> PetSessionPolicy:
    """从 FastAPI 应用状态读取 PetSessionPolicy。

    :param request: 当前 HTTP 请求对象。
    :return: 已装配且具备执行条件的 PetSessionPolicy。
    :raises RuntimeError: 当 PetSessionPolicy 尚未装配或未就绪时抛出。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    policy = getattr(app_state, "pet_session_policy", None)
    policy_ready = getattr(app_state, "pet_session_policy_ready", False)
    if policy is None or not policy_ready or not policy.is_ready():
        raise RuntimeError("PetSessionPolicy 尚未初始化或未就绪")
    return policy


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


def _build_disabled_response(
    request: Request,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建 API 接入组件被禁用时的统一错误响应。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :return: 表示 API 接入组件已禁用的 JSON 响应。
    """

    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=503,
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id=_get_header_value(
            request,
            settings.request_identity.request_id_header,
            REQUEST_ID_FALLBACK,
        ),
        trace_id=_get_header_value(
            request,
            settings.request_identity.trace_id_header,
            TRACE_ID_FALLBACK,
        ),
        public_message="api ingress is disabled",
        details=[ErrorDetailDto(field="api_ingress.enabled", reason="disabled")],
    )


def _build_openai_compatibility_disabled_response(
    request: Request,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建 OpenAI 兼容入口被禁用时的统一错误响应。

    :param request: 当前 HTTP 请求对象。
    :param settings: API 接入组件配置。
    :return: 表示 OpenAI 兼容入口未开放的 JSON 响应。
    """

    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=404,
        code=IngressErrorCode.INVALID_REQUEST,
        request_id=_get_header_value(
            request,
            settings.request_identity.request_id_header,
            REQUEST_ID_FALLBACK,
        ),
        trace_id=_get_header_value(
            request,
            settings.request_identity.trace_id_header,
            TRACE_ID_FALLBACK,
        ),
        public_message="openai compatibility endpoint is disabled",
        details=[
            ErrorDetailDto(field="openai_compatibility.enabled", reason="disabled")
        ],
    )


class _ApiIngressAvailabilityRoute(APIRoute):
    """API 接入组件可用性前置检查路由。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """创建带有 ApiIngress 启用状态检查的路由处理函数。

        :return: 包装后的 FastAPI 路由处理函数。
        """

        original_route_handler = super().get_route_handler()

        async def guarded_route_handler(request: Request) -> Response:
            """在 DTO Validation 前检查 API 接入组件是否启用。

            :param request: 当前 HTTP 请求对象。
            :return: 组件禁用时返回统一错误响应，否则返回原路由处理结果。
            """

            settings = _get_api_ingress_settings(request)
            if not settings.enabled:
                return _build_disabled_response(request=request, settings=settings)
            return await original_route_handler(request)

        return guarded_route_handler


def _build_todo_dependency_response(
    built_request: AgentTurnRequestCommandDto,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建领域依赖尚未接入时的统一 TODO 响应。

    :param built_request: 已完成 AgentTurnRequest Builder 处理的请求命令 DTO。
    :param settings: API 接入组件配置。
    :return: 表示编排层暂不可用的 JSON 响应。
    """

    request_context = built_request.request_context
    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=503,
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        public_message="service unavailable",
        diagnostic_message="api ingress downstream service is not implemented",
        details=[
            ErrorDetailDto(field="route_kind", reason=request_context.route_kind.value),
            ErrorDetailDto(
                field="response_mode",
                reason=request_context.response_mode.value,
            ),
            ErrorDetailDto(
                field="idempotency_key", reason=built_request.idempotency_key
            ),
            ErrorDetailDto(field="orchestrator", reason="todo_placeholder"),
        ],
        source=DEPENDENCY_ERROR_SOURCE,
    )


def _build_rate_limit_response(
    normalized_request: AgentTurnInternalRequestDto,
    decision: ApiIngressRateLimitDecision,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建 API 接入限流命中时的统一错误响应。

    :param normalized_request: 已完成 Ingress Normalizer 处理的内部请求 DTO。
    :param decision: API 接入组件限流判定结果。
    :param settings: API 接入组件配置。
    :return: 表示当前请求已被入口限流的 JSON 响应。
    """

    request_context = normalized_request.request_context
    headers: dict[str, str] = {}
    if decision.retry_after_seconds is not None:
        headers["Retry-After"] = str(decision.retry_after_seconds)
    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=429,
        code=IngressErrorCode.RATE_LIMITED,
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        public_message="api ingress rate limit exceeded",
        details=decision.details,
        headers=headers,
    )


def _build_concurrency_limit_response(
    built_request: AgentTurnRequestCommandDto,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建编排入口并发闸门已满时的统一错误响应。

    :param built_request: 已完成 AgentTurnRequest Builder 处理的请求命令 DTO。
    :param settings: API 接入组件配置。
    :return: 表示编排入口当前并发已满的 JSON 响应。
    """

    request_context = built_request.request_context
    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=503,
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        public_message="orchestrator concurrency limit exceeded",
        details=[
            ErrorDetailDto(field="orchestrator.max_concurrency", reason="exceeded")
        ],
    )


def _build_validation_failure_response(
    failure: ApiIngressValidationFailure,
) -> JSONResponse:
    """构建 API 接入 DTO 后置校验失败响应。

    :param failure: API 接入 DTO 后置校验失败结果。
    :return: 使用统一错误结构包装后的 JSON 响应。
    """

    return JSONResponse(
        status_code=failure.status_code,
        content=failure.error_response.model_dump(mode="json"),
    )


def _build_identity_failure_response(
    failure: RequestIdentityResolutionFailure,
) -> JSONResponse:
    """构建 API 接入请求身份解析失败响应。

    :param failure: API 接入请求身份解析失败结果。
    :return: 使用统一错误结构包装后的 JSON 响应。
    """

    return JSONResponse(
        status_code=failure.status_code,
        content=failure.error_response.model_dump(mode="json"),
    )


def _build_parse_failure_response(
    failure: ApiIngressRequestParseFailure,
) -> JSONResponse:
    """构建 API 接入请求解析失败响应。

    :param failure: API 接入请求解析失败结果。
    :return: 使用统一错误结构包装后的 JSON 响应。
    """

    return JSONResponse(
        status_code=failure.status_code,
        content=failure.error_response.model_dump(mode="json"),
    )


def _build_pet_session_request_context(
    built_request: AgentTurnRequestCommandDto,
) -> PetSessionRequestContextDto:
    """从编排请求命令构建 PetSessionPolicy 请求上下文。

    :param built_request: 已完成 AgentTurnRequest Builder 处理的请求命令 DTO。
    :return: 可传入 PetSessionPolicy 的请求上下文 DTO。
    """

    request_context = built_request.request_context
    trusted_identity = built_request.trusted_identity
    return PetSessionRequestContextDto(
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        user_id=trusted_identity.user_id,
        session_id=trusted_identity.session_id,
        pet_id=trusted_identity.pet_id,
        client_pet_snapshot_ref=(
            dict(trusted_identity.pet_info)
            if trusted_identity.pet_info is not None
            else None
        ),
    )


def _resolve_pet_session_error_response_mapping(
    error: PetSessionPolicyError,
) -> tuple[
    int,
    IngressErrorCode,
    str,
    ApiIngressErrorResponseSource,
]:
    """解析 PetSessionPolicy 领域错误的 HTTP 与入口错误映射。

    :param error: PetSessionPolicy 领域异常。
    :return: HTTP 状态码、入口错误码、稳定公开消息与错误来源分类。
    """

    if error.code is PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING:
        return (
            422,
            IngressErrorCode.MISSING_REQUIRED_CONTEXT,
            "required pet session context is missing",
            CLIENT_ERROR_SOURCE,
        )
    if error.code is PetSessionPolicyErrorCode.PET_MISMATCH:
        return (
            409,
            IngressErrorCode.INVALID_REQUEST,
            "session is bound to another pet; create a new session",
            CLIENT_ERROR_SOURCE,
        )
    if error.code is PetSessionPolicyErrorCode.USER_MISMATCH:
        return (
            409,
            IngressErrorCode.INVALID_REQUEST,
            "session identity does not match the request",
            CLIENT_ERROR_SOURCE,
        )
    if error.code is PetSessionPolicyErrorCode.SESSION_CLOSED:
        return (
            409,
            IngressErrorCode.INVALID_REQUEST,
            "session is closed",
            CLIENT_ERROR_SOURCE,
        )
    if error.code is PetSessionPolicyErrorCode.SESSION_ARCHIVED:
        return (
            409,
            IngressErrorCode.INVALID_REQUEST,
            "session is archived",
            CLIENT_ERROR_SOURCE,
        )
    if error.code in {
        PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
        PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE,
        PetSessionPolicyErrorCode.POLICY_DISABLED,
    }:
        return (
            503,
            IngressErrorCode.SERVICE_UNAVAILABLE,
            "pet session policy is unavailable",
            DEPENDENCY_ERROR_SOURCE,
        )
    return (
        500,
        IngressErrorCode.INTERNAL_ERROR,
        "pet session policy failed",
        INTERNAL_ERROR_SOURCE,
    )


def _build_pet_session_policy_error_response(
    *,
    error: PetSessionPolicyError,
    settings: ApiIngressSettings,
) -> JSONResponse:
    """构建 PetSessionPolicy 阻断结果对应的统一 JSON 错误响应。

    :param error: PetSessionPolicy 领域异常。
    :param settings: API 接入组件配置。
    :return: 已按入口层错误策略裁剪与脱敏的 JSON 错误响应。
    """

    status_code, ingress_code, public_message, source = (
        _resolve_pet_session_error_response_mapping(error)
    )
    error_dto = error.to_dto()
    details = [
        ErrorDetailDto(
            field="pet_session_policy.error_code",
            reason=error_dto.code.value,
        ),
        ErrorDetailDto(
            field="pet_session_policy.decision",
            reason=error_dto.decision.decision.value,
        ),
        ErrorDetailDto(
            field="pet_session_policy.trace_delivery",
            reason=error_dto.trace_delivery_status.value,
        ),
    ]
    if error_dto.decision.missing_field is not None:
        details.append(
            ErrorDetailDto(
                field=f"vet_context.{error_dto.decision.missing_field}",
                reason="missing",
            )
        )
    return build_api_ingress_json_error_response(
        settings=settings,
        status_code=status_code,
        code=ingress_code,
        request_id=error_dto.request_id,
        trace_id=error_dto.trace_id,
        public_message=public_message,
        diagnostic_message=error_dto.message,
        details=details,
        source=source,
    )


async def _handle_turn_request(
    request: Request,
    route_kind: ApiRouteKind,
) -> JSONResponse:
    """处理 API 接入一轮对话请求的公共链路。

    :param request: 当前 HTTP 请求对象。
    :param route_kind: 当前入口路由类型。
    :return: 当前阶段的 TODO 统一错误响应。
    """

    settings = _get_api_ingress_settings(request)
    parse_result = await parse_agent_turn_request(request=request, settings=settings)
    if parse_result.failure is not None:
        return _build_parse_failure_response(parse_result.failure)
    turn_request = parse_result.turn_request
    if turn_request is None:
        raise RuntimeError("请求解析未返回可用 DTO")
    identity_resolution = resolve_request_identity(
        request=request,
        turn_request=turn_request,
        settings=settings,
    )
    if identity_resolution.failure is not None:
        return _build_identity_failure_response(identity_resolution.failure)
    identity_context = identity_resolution.identity_context
    if identity_context is None:
        raise RuntimeError("请求身份解析未返回可用上下文")
    observability_provider = _get_observability_provider(request)
    if observability_provider is not None:
        observability_provider.bind_request_identity(
            request_id=identity_context.request_id,
            trace_id=identity_context.trace_id,
            safe_attributes={
                "request_id_source": identity_context.request_id_source,
                "trace_id_source": identity_context.trace_id_source,
            },
        )
    validation_failure = await validate_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=route_kind,
        identity_context=identity_context,
    )
    if validation_failure is not None:
        return _build_validation_failure_response(validation_failure)
    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=route_kind,
        identity_context=identity_context,
    )
    response_mode_failure = validate_response_mode_availability(
        normalized_request=normalized_request,
        settings=settings,
    )
    if response_mode_failure is not None:
        return _build_validation_failure_response(response_mode_failure)

    rate_limiter = _get_api_ingress_rate_limiter(request)
    rate_limit_decision = await rate_limiter.try_acquire(
        request=request,
        response_mode=normalized_request.request_context.response_mode,
    )
    if not rate_limit_decision.allowed:
        return _build_rate_limit_response(
            normalized_request=normalized_request,
            decision=rate_limit_decision,
            settings=settings,
        )
    stream_lease = rate_limit_decision.stream_lease
    try:
        built_request = build_agent_turn_request(
            normalized_request=normalized_request,
            settings=settings,
        )
        pet_session_policy = _get_pet_session_policy(request)
        try:
            pet_session_context: PetSessionContextDto = (
                await pet_session_policy.ensure_context(
                    _build_pet_session_request_context(built_request)
                )
            )
        except PetSessionPolicyError as exc:
            return _build_pet_session_policy_error_response(
                error=exc,
                settings=settings,
            )
        concurrency_gate = _get_orchestrator_concurrency_gate(request)
        concurrency_lease = await concurrency_gate.try_acquire()
        if concurrency_lease is None:
            return _build_concurrency_limit_response(
                built_request=built_request,
                settings=settings,
            )
        try:
            del pet_session_context
            return _build_todo_dependency_response(
                built_request=built_request,
                settings=settings,
            )
        finally:
            await concurrency_lease.release()
    finally:
        if stream_lease is not None:
            await stream_lease.release()


async def handle_agent_turns(
    request: Request,
) -> JSONResponse:
    """处理生产主业务对话入口。

    :param request: 当前 HTTP 请求对象。
    :return: 当前阶段的 TODO 统一错误响应。
    """

    return await _handle_turn_request(
        request=request,
        route_kind=ApiRouteKind.AGENT_TURNS,
    )


async def handle_openai_responses(
    request: Request,
) -> JSONResponse:
    """处理 OpenAI Responses 风格兼容入口。

    :param request: 当前 HTTP 请求对象。
    :return: 当前阶段的 TODO 统一错误响应。
    """

    settings = _get_api_ingress_settings(request)
    if not settings.openai_compatibility.enabled:
        return _build_openai_compatibility_disabled_response(
            request=request,
            settings=settings,
        )
    return await _handle_turn_request(
        request=request,
        route_kind=ApiRouteKind.OPENAI_RESPONSES,
    )


def create_api_ingress_router() -> APIRouter:
    """创建 API 接入组件 FastAPI 路由器。

    :return: 已注册对话入口的 FastAPI 路由器。
    """

    router = APIRouter(route_class=_ApiIngressAvailabilityRoute, tags=["ApiIngress"])
    router.add_api_route(
        "/agent/turns",
        handle_agent_turns,
        methods=["POST"],
    )
    router.add_api_route(
        "/openai/v1/responses",
        handle_openai_responses,
        methods=["POST"],
    )
    return router


__all__: tuple[str, ...] = (
    "create_api_ingress_router",
    "handle_agent_turns",
    "handle_openai_responses",
)
