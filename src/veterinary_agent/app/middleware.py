##################################################################################################
# 文件: src/veterinary_agent/app/middleware.py
# 作用: 定义 ASGI / FastAPI 框架层中间件，承载请求耗时等通用 HTTP 外壳能力。
# 边界: 不读取或记录完整医疗正文，不执行 ApiIngress 校验、归一化、编排调用或业务判断。
##################################################################################################

from time import perf_counter
from typing import Awaitable, Callable, Final

from fastapi import FastAPI, Request, Response

PROCESS_TIME_HEADER: Final[str] = "X-Process-Time-Ms"


async def add_process_time_header(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """为 HTTP 响应附加请求处理耗时。

    :param request: 当前 HTTP 请求对象。
    :param call_next: 下一个 ASGI 请求处理器。
    :return: 已附加耗时 Header 的 HTTP 响应。
    """

    started_at = perf_counter()
    response = await call_next(request)
    duration_ms = (perf_counter() - started_at) * 1000
    response.headers[PROCESS_TIME_HEADER] = f"{duration_ms:.3f}"
    return response


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
