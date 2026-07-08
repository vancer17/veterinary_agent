##################################################################################################
# 文件: src/veterinary_agent/app/lifespan.py
# 作用: 定义 FastAPI 应用生命周期，负责框架层配置加载、应用状态初始化与关闭期状态清理。
# 边界: 当前仅装配 ASGI 框架状态；编排层、存储层、可观测性客户端等依赖后续以 TODO 方式接入。
##################################################################################################

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.app.dependencies import APP_STATE_KEY
from veterinary_agent.app.state import VeterinaryAgentAppState
from veterinary_agent.config import ApiIngressSettings, load_api_ingress_settings

LifespanHandler = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_lifespan(settings: ApiIngressSettings | None = None) -> LifespanHandler:
    """创建 FastAPI lifespan 处理器。

    :param settings: 可选的 API 接入组件配置；未传入时从默认配置源加载。
    :return: 可传入 FastAPI 的 lifespan 处理器。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """管理 FastAPI 应用启动与关闭流程。

        :param app: 当前 FastAPI 应用实例。
        :return: 异步上下文迭代器，无业务返回值。
        """

        resolved_settings = (
            settings if settings is not None else load_api_ingress_settings()
        )
        app_state = VeterinaryAgentAppState(
            settings=resolved_settings,
            started_at=datetime.now(UTC),
            ready=True,
            orchestrator_concurrency_gate=ApiIngressConcurrencyGate(
                max_concurrency=resolved_settings.orchestrator.max_concurrency,
            ),
            rate_limiter=ApiIngressRateLimiter.from_settings(resolved_settings),
        )
        setattr(app.state, APP_STATE_KEY, app_state)
        try:
            yield
        finally:
            app_state.ready = False

    return lifespan


__all__: tuple[str, ...] = (
    "LifespanHandler",
    "create_lifespan",
)
