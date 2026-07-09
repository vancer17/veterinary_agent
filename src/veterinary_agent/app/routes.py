##################################################################################################
# 文件: src/veterinary_agent/app/routes.py
# 作用: 定义 ASGI 框架层基础探针路由，提供 /health 与消费 readiness 配置的 /ready。
# 边界: 不实现编排层真实健康探测；领域外依赖未接入时由 ApiIngress readiness checker 返回 TODO 占位。
##################################################################################################

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from veterinary_agent.api_ingress import (
    DEPENDENCY_ERROR_SOURCE,
    ErrorResponseDto,
    HealthResponseDto,
    IngressErrorCode,
    ReadyResponseDto,
    build_api_ingress_json_error_response,
    check_api_ingress_readiness,
)
from veterinary_agent.app.dependencies import get_app_state
from veterinary_agent.app.state import VeterinaryAgentAppState
from veterinary_agent.observability import PROMETHEUS_CONTENT_TYPE


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


async def health() -> HealthResponseDto:
    """返回进程存活状态。

    :return: 存活检查响应 DTO。
    """

    return HealthResponseDto()


async def ready(
    request: Request,
    state: Annotated[VeterinaryAgentAppState, Depends(get_app_state)],
) -> ReadyResponseDto | JSONResponse:
    """返回服务就绪状态。

    :param request: 当前 HTTP 请求对象。
    :param state: 当前 FastAPI 应用框架级状态。
    :return: 就绪成功 DTO，或统一错误结构 JSON 响应。
    """

    readiness_result = check_api_ingress_readiness(
        settings=state.settings,
        app_ready=state.ready,
        runtime_config_ready=(
            state.runtime_config_provider is not None
            and state.runtime_config_snapshot is not None
            and state.runtime_config_provider.is_ready()
        ),
        checkpoint_store_runtime_config_ready=(
            state.checkpoint_store_settings is not None
        ),
        observability_ready=(
            state.observability_provider is not None
            and state.observability_ready
            and state.observability_provider.is_ready()
        ),
    )
    if readiness_result.ready:
        return ReadyResponseDto()

    request_id = _get_header_value(
        request, state.settings.request_identity.request_id_header, "req_unavailable"
    )
    trace_id = _get_header_value(
        request, state.settings.request_identity.trace_id_header, "trace_unavailable"
    )
    return build_api_ingress_json_error_response(
        settings=state.settings,
        status_code=503,
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id=request_id,
        trace_id=trace_id,
        public_message="service is not ready",
        details=readiness_result.details,
        source=DEPENDENCY_ERROR_SOURCE,
    )


async def metrics(
    state: Annotated[VeterinaryAgentAppState, Depends(get_app_state)],
) -> Response:
    """返回 Prometheus 文本格式指标。

    :param state: 当前 FastAPI 应用框架级状态。
    :return: Prometheus 文本响应；provider 缺失或 endpoint 关闭时返回错误状态。
    """

    provider = state.observability_provider
    if provider is None or not provider.is_ready():
        return JSONResponse(
            status_code=503,
            content={
                "code": "OBS_EXPORTER_UNAVAILABLE",
                "message": "observability provider is unavailable",
            },
        )
    if not provider.metrics_endpoint_enabled():
        return JSONResponse(
            status_code=404,
            content={
                "code": "OBS_METRICS_ENDPOINT_UNAVAILABLE",
                "message": "metrics endpoint is disabled",
            },
        )
    return Response(
        content=provider.render_prometheus_metrics(),
        media_type=PROMETHEUS_CONTENT_TYPE,
    )


def create_framework_router(metrics_path: str = "/metrics") -> APIRouter:
    """创建 ASGI 框架层基础探针路由。

    :param metrics_path: Observability metrics endpoint 路径。
    :return: 已注册 /health 与 /ready 的 FastAPI 路由器。
    """

    router = APIRouter()
    router.add_api_route(
        "/health", health, methods=["GET"], response_model=HealthResponseDto
    )
    router.add_api_route(
        "/ready",
        ready,
        methods=["GET"],
        response_model=ReadyResponseDto,
        responses={503: {"model": ErrorResponseDto}},
    )
    router.add_api_route(
        metrics_path,
        metrics,
        methods=["GET"],
        response_model=None,
        response_class=Response,
    )
    return router


__all__: tuple[str, ...] = (
    "create_framework_router",
    "health",
    "metrics",
    "ready",
)
