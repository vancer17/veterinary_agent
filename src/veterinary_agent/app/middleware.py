##################################################################################################
# 文件: src/veterinary_agent/app/middleware.py
# 作用: 定义 ASGI / FastAPI 框架层中间件，承载请求耗时等通用 HTTP 外壳能力。
# 边界: 不读取或记录完整医疗正文，不执行 ApiIngress 校验、归一化、编排调用或业务判断。
##################################################################################################

from time import perf_counter
from typing import Awaitable, Callable, Final

from fastapi import FastAPI, Request, Response

from veterinary_agent.core import APP_STATE_KEY
from veterinary_agent.app.state import VeterinaryAgentAppState
from veterinary_agent.observability import (
    ObservabilityProvider,
    RequestObservationHandle,
)

PROCESS_TIME_HEADER: Final[str] = "X-Process-Time-Ms"
REQUEST_ID_FALLBACK: Final[str] = "req_unavailable"
TRACE_ID_FALLBACK: Final[str] = "trace_unavailable"


def _get_observability_provider(request: Request) -> ObservabilityProvider | None:
    """从 FastAPI app.state 读取 Observability provider。

    :param request: 当前 HTTP 请求对象。
    :return: 已装配的 Observability provider；未装配或未就绪时返回 None。
    """

    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    if not isinstance(app_state, VeterinaryAgentAppState):
        return None
    provider = app_state.observability_provider
    if provider is None or not provider.is_ready():
        return None
    return provider


def _get_header_value(request: Request, header_name: str, fallback: str) -> str:
    """读取 HTTP Header 值。

    :param request: 当前 HTTP 请求对象。
    :param header_name: 需要读取的 Header 名称。
    :param fallback: Header 缺失时使用的兜底值。
    :return: Header 值或兜底值。
    """

    value = request.headers.get(header_name)
    if value:
        return value
    return fallback


def _is_streaming_request(request: Request) -> bool:
    """基于请求头粗略判断请求是否偏好流式响应。

    :param request: 当前 HTTP 请求对象。
    :return: 若 Accept 包含 text/event-stream 则返回 True。
    """

    accept_header = request.headers.get("accept", "")
    return "text/event-stream" in accept_header.lower()


def _start_observation(
    *,
    request: Request,
    provider: ObservabilityProvider | None,
) -> RequestObservationHandle | None:
    """启动 HTTP 请求观测。

    :param request: 当前 HTTP 请求对象。
    :param provider: Observability provider；为空时跳过观测。
    :return: 请求观测句柄；无法观测时返回 None。
    """

    if provider is None:
        return None
    app_state = getattr(request.app.state, APP_STATE_KEY, None)
    settings = getattr(app_state, "settings", None)
    runtime_config_snapshot = getattr(app_state, "runtime_config_snapshot", None)
    request_id_header = getattr(
        getattr(settings, "request_identity", None),
        "request_id_header",
        "X-Request-ID",
    )
    trace_id_header = getattr(
        getattr(settings, "request_identity", None),
        "trace_id_header",
        "X-Trace-ID",
    )
    return provider.start_request(
        request_id=_get_header_value(request, request_id_header, REQUEST_ID_FALLBACK),
        trace_id=_get_header_value(request, trace_id_header, TRACE_ID_FALLBACK),
        endpoint=request.url.path,
        method=request.method,
        streaming=_is_streaming_request(request),
        config_snapshot_id=getattr(runtime_config_snapshot, "config_snapshot_id", None),
        params_version=getattr(runtime_config_snapshot, "params_version", None),
    )


async def add_process_time_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """为 HTTP 响应附加请求处理耗时。

    :param request: 当前 HTTP 请求对象。
    :param call_next: 下一个 ASGI 请求处理器。
    :return: 已附加耗时 Header 的 HTTP 响应。
    """

    started_at = perf_counter()
    provider = _get_observability_provider(request)
    observation_handle = _start_observation(request=request, provider=provider)
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        if provider is not None:
            provider.record_error(
                component="FastAPI",
                error_type=type(exc).__name__,
                error_message="request handling failed",
                safe_fields={"endpoint": request.url.path, "method": request.method},
            )
        raise
    finally:
        duration_ms = (perf_counter() - started_at) * 1000
        if response is not None:
            response.headers[PROCESS_TIME_HEADER] = f"{duration_ms:.3f}"
        if provider is not None and observation_handle is not None:
            provider.finish_request(
                handle=observation_handle,
                status_code=response.status_code if response is not None else 500,
                error_type=None if response is not None else "unhandled_exception",
            )


def register_middlewares(app: FastAPI) -> None:
    """注册 FastAPI 框架层中间件。

    :param app: 需要注册中间件的 FastAPI 应用实例。
    :return: 无返回值。
    """

    app.middleware("http")(add_process_time_header)


__all__: tuple[str, ...] = (
    "PROCESS_TIME_HEADER",
    "add_process_time_header",
    "register_middlewares",
)
