##################################################################################################
# 文件: tests/llm_gateway/test_app_integration.py
# 作用: 验证 LlmGateway 配置、工厂装配和 readiness 探针的集成契约。
# 边界: 使用 fake provider adapter，不连接真实数据库或模型代理。
##################################################################################################

from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.api_ingress import check_api_ingress_readiness
from veterinary_agent.llm_gateway import create_default_llm_gateway

from . import FakeProviderAdapter, build_success_response, build_test_settings


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
    """验证可通过 fake adapter 构造真实 DefaultLlmGateway。

    :return: None。
    """

    adapter = FakeProviderAdapter(response=build_success_response())
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    assert gateway.is_ready() is True
