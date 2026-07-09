##################################################################################################
# 文件: src/veterinary_agent/app/factory.py
# 作用: 创建并装配 FastAPI ASGI 应用，集中注册生命周期、中间件、异常处理器与入口路由。
# 边界: 仅装配 ASGI App / Framework 与 ApiIngress Router 外壳，不实现编排调用或兽医业务逻辑。
##################################################################################################

from fastapi import FastAPI

from veterinary_agent.api_ingress import create_api_ingress_router
from veterinary_agent.app.exception_handlers import register_exception_handlers
from veterinary_agent.app.lifespan import CheckpointProviderFactory, create_lifespan
from veterinary_agent.app.middleware import register_middlewares
from veterinary_agent.app.routes import create_framework_router
from veterinary_agent.config import ApiIngressSettings, CheckpointStoreSettings


def create_app(
    settings: ApiIngressSettings | None = None,
    checkpoint_store_settings: CheckpointStoreSettings | None = None,
    checkpoint_provider_factory: CheckpointProviderFactory | None = None,
) -> FastAPI:
    """创建 FastAPI ASGI 应用实例。

    :param settings: 可选的 API 接入组件配置；未传入时由生命周期函数加载默认配置。
    :param checkpoint_store_settings: 可选 CheckpointStore RuntimeConfig；未传入时由生命周期函数加载默认配置。
    :param checkpoint_provider_factory: 可选 checkpoint provider 工厂；测试可注入 TODO 空壳避免连接真实数据库。
    :return: 已完成框架层装配的 FastAPI 应用实例。
    """

    app = FastAPI(
        title="Veterinary Agent",
        version="0.1.0",
        description="兽医 Agent API 服务。当前装配 ASGI App / Framework 与 ApiIngress Router 外壳。",
        lifespan=create_lifespan(
            settings=settings,
            checkpoint_store_settings=checkpoint_store_settings,
            checkpoint_provider_factory=checkpoint_provider_factory,
        ),
    )
    register_middlewares(app)
    register_exception_handlers(app)
    app.include_router(create_framework_router())
    app.include_router(create_api_ingress_router())
    return app


__all__: tuple[str, ...] = ("create_app",)
