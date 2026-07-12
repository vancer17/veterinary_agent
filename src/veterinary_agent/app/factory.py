##################################################################################################
# 文件: src/veterinary_agent/app/factory.py
# 作用: 创建并装配 FastAPI ASGI 应用，集中注册生命周期、中间件、异常处理器与入口路由。
# 边界: 仅装配 ASGI App / Framework 与 ApiIngress Router 外壳，不实现编排调用或兽医业务逻辑。
##################################################################################################

from fastapi import FastAPI

from veterinary_agent.api_ingress import create_api_ingress_router
from veterinary_agent.app.bootstrap import (
    AgentApplicationServiceFactory,
    AgentGraphRuntimeFactory,
    AgentRunnerFactory,
    CheckpointProviderFactory,
    ConversationStoreFactory,
    LlmGatewayFactory,
    LogicTraceStoreFactory,
)
from veterinary_agent.app.exception_handlers import register_exception_handlers
from veterinary_agent.app.lifespan import create_lifespan
from veterinary_agent.app.middleware import register_middlewares
from veterinary_agent.app.routes import create_framework_router
from veterinary_agent.config import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    ConversationStoreSettings,
    LlmGatewaySettings,
    ObservabilitySettings,
    RuntimeConfigSettings,
    load_observability_settings,
)


def create_app(
    settings: ApiIngressSettings | None = None,
    checkpoint_store_settings: CheckpointStoreSettings | None = None,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    runtime_config_settings: RuntimeConfigSettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    checkpoint_provider_factory: CheckpointProviderFactory | None = None,
    conversation_store_factory: ConversationStoreFactory | None = None,
    llm_gateway_factory: LlmGatewayFactory | None = None,
    agent_runner_factory: AgentRunnerFactory | None = None,
    graph_runtime_factory: AgentGraphRuntimeFactory | None = None,
    logic_trace_store_factory: LogicTraceStoreFactory | None = None,
    agent_application_service_factory: AgentApplicationServiceFactory | None = None,
) -> FastAPI:
    """创建 FastAPI ASGI 应用实例。

    :param settings: 可选的 API 接入组件配置；未传入时由生命周期函数加载默认配置。
    :param checkpoint_store_settings: 可选 CheckpointStore RuntimeConfig；未传入时由生命周期函数加载默认配置。
    :param conversation_store_settings: 可选 ConversationStore RuntimeConfig；未传入时由生命周期函数加载默认配置。
    :param llm_gateway_settings: 可选 LlmGateway RuntimeConfig；未传入时由生命周期函数加载默认配置。
    :param runtime_config_settings: 可选 RuntimeConfig 组件自身配置；未传入时由生命周期函数加载默认配置。
    :param observability_settings: 可选 Observability RuntimeConfig；未传入时由生命周期函数加载默认配置。
    :param checkpoint_provider_factory: 可选 checkpoint provider 工厂；测试可注入 TODO 空壳避免连接真实数据库。
    :param conversation_store_factory: 可选 ConversationStore 工厂；测试或业务装配可注入真实实现。
    :param llm_gateway_factory: 可选 LlmGateway 工厂；测试可注入 fake 实现。
    :param agent_runner_factory: 可选 AgentRunner 工厂；测试或业务装配可注入真实实现。
    :param graph_runtime_factory: 可选 GraphRuntime 工厂；测试或后续业务装配可注入真实实现。
    :param logic_trace_store_factory: 可选 LogicTraceStore 工厂；测试或后续业务装配可注入真实实现。
    :param agent_application_service_factory: 可选 AgentApplicationService 工厂；未传入时使用默认胶水层实现。
    :return: 已完成框架层装配的 FastAPI 应用实例。
    """

    resolved_observability_settings = (
        observability_settings
        if observability_settings is not None
        else load_observability_settings()
    )
    app = FastAPI(
        title="Veterinary Agent",
        version="0.1.0",
        description="兽医 Agent API 服务。当前装配 ASGI App / Framework 与 ApiIngress Router 外壳。",
        lifespan=create_lifespan(
            settings=settings,
            checkpoint_store_settings=checkpoint_store_settings,
            conversation_store_settings=conversation_store_settings,
            llm_gateway_settings=llm_gateway_settings,
            runtime_config_settings=runtime_config_settings,
            observability_settings=resolved_observability_settings,
            checkpoint_provider_factory=checkpoint_provider_factory,
            conversation_store_factory=conversation_store_factory,
            llm_gateway_factory=llm_gateway_factory,
            agent_runner_factory=agent_runner_factory,
            graph_runtime_factory=graph_runtime_factory,
            logic_trace_store_factory=logic_trace_store_factory,
            agent_application_service_factory=agent_application_service_factory,
        ),
    )
    register_middlewares(app)
    register_exception_handlers(app)
    app.include_router(
        create_framework_router(
            metrics_path=resolved_observability_settings.metrics.endpoint_path,
        )
    )
    app.include_router(create_api_ingress_router())
    return app


__all__: tuple[str, ...] = ("create_app",)
