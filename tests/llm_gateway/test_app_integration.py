##################################################################################################
# 文件: tests/llm_gateway/test_app_integration.py
# 作用: 验证 LlmGateway RuntimeConfig、应用状态、依赖函数和 readiness 探针的集成契约。
# 边界: 使用 fake checkpoint provider，不连接真实数据库、不访问真实模型代理。
##################################################################################################

from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI, Request

from veterinary_agent import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
    ApiIngressSettings,
    CheckpointStoreSettings,
    ConversationStoreSettings,
    DefaultPetSessionPolicy,
    LlmGateway,
    LlmGatewayError,
    TodoConversationStore,
    VeterinaryAgentAppState,
    check_api_ingress_readiness,
    create_default_llm_gateway,
    create_runtime_config_provider,
    get_llm_gateway,
    get_llm_gateway_settings,
)

from . import (
    FakeProviderAdapter,
    build_success_response,
    build_test_settings,
)


class FakeLlmGateway:
    """应用依赖测试用 LlmGateway。"""

    def __init__(self, *, ready: bool) -> None:
        """初始化 fake LlmGateway。

        :param ready: fake gateway 是否报告就绪。
        :return: None。
        """

        self.ready = ready
        self.close_calls = 0

    def is_ready(self) -> bool:
        """判断 fake gateway 是否就绪。

        :return: 当前 ready 标记。
        """

        return self.ready

    async def close(self) -> None:
        """关闭 fake gateway。

        :return: None。
        """

        self.close_calls += 1
        self.ready = False


def _build_state(*, llm_gateway_ready: bool) -> VeterinaryAgentAppState:
    """构建带 LlmGateway 的测试 app state。

    :param llm_gateway_ready: app state 中 LlmGateway 就绪标记。
    :return: VeterinaryAgentAppState。
    """

    settings = ApiIngressSettings()
    checkpoint_settings = CheckpointStoreSettings()
    conversation_settings = ConversationStoreSettings()
    llm_settings = build_test_settings()
    runtime_provider = create_runtime_config_provider(
        api_ingress_settings=settings,
        checkpoint_store_settings=checkpoint_settings,
        conversation_store_settings=conversation_settings,
        llm_gateway_settings=llm_settings,
    )
    snapshot = runtime_provider.current_snapshot()
    conversation_store = TodoConversationStore()
    pet_policy = DefaultPetSessionPolicy(
        conversation_store=conversation_store,
        runtime_config_provider=runtime_provider,
    )
    gateway = cast(LlmGateway, FakeLlmGateway(ready=llm_gateway_ready))
    return VeterinaryAgentAppState(
        settings=settings,
        runtime_config_provider=runtime_provider,
        runtime_config_snapshot=snapshot,
        started_at=datetime.now(UTC),
        ready=True,
        orchestrator_concurrency_gate=ApiIngressConcurrencyGate(
            max_concurrency=settings.orchestrator.max_concurrency,
        ),
        rate_limiter=ApiIngressRateLimiter.from_settings(settings),
        checkpoint_store_settings=checkpoint_settings,
        checkpoint_provider=None,
        checkpoint_provider_ready=False,
        checkpoint_provider_error=None,
        conversation_store_settings=conversation_settings,
        conversation_store=conversation_store,
        conversation_store_ready=True,
        conversation_store_error=None,
        pet_session_policy=pet_policy,
        pet_session_policy_ready=True,
        llm_gateway_settings=llm_settings,
        llm_gateway=gateway,
        llm_gateway_ready=llm_gateway_ready,
    )


def _request(app_state: VeterinaryAgentAppState) -> Request:
    """构建可传入依赖函数的 FastAPI Request。

    :param app_state: 需要挂载的测试 app state。
    :return: FastAPI Request。
    """

    app = FastAPI()
    setattr(app.state, "veterinary_agent_state", app_state)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
        }
    )


def test_get_llm_gateway_dependencies() -> None:
    """验证 app dependency 可读取 LlmGateway 配置与实例。

    :return: None。
    """

    app_state = _build_state(llm_gateway_ready=True)
    request = _request(app_state)

    assert get_llm_gateway_settings(request) is app_state.llm_gateway_settings
    assert get_llm_gateway(request) is app_state.llm_gateway


def test_get_llm_gateway_rejects_not_ready_gateway() -> None:
    """验证 LlmGateway 未就绪时 dependency 抛出稳定错误。

    :return: None。
    """

    request = _request(_build_state(llm_gateway_ready=False))

    with pytest.raises(LlmGatewayError):
        get_llm_gateway(request)


def test_readiness_requires_llm_gateway_only_when_enabled() -> None:
    """验证 readiness 仅在 LlmGateway 启用时要求其就绪。

    :return: None。
    """

    settings = ApiIngressSettings()
    disabled_result = check_api_ingress_readiness(
        settings=settings,
        app_ready=True,
        agent_application_service_ready=True,
        llm_gateway_required=False,
        llm_gateway_ready=False,
    )
    enabled_result = check_api_ingress_readiness(
        settings=settings,
        app_ready=True,
        agent_application_service_ready=True,
        llm_gateway_required=True,
        llm_gateway_ready=False,
    )

    assert disabled_result.ready is True
    assert {detail.field for detail in enabled_result.details} >= {"llm_gateway"}


def test_gateway_factory_can_be_constructed_with_fake_adapter() -> None:
    """验证测试可通过 fake adapter 构造真实 DefaultLlmGateway。

    :return: None。
    """

    adapter = FakeProviderAdapter(response=build_success_response())
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    assert gateway.is_ready() is True
