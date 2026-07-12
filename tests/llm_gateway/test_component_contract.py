##################################################################################################
# 文件: tests/llm_gateway/test_component_contract.py
# 作用: 验证 LlmGateway 公共契约、配置关系、RuntimeConfig 命名空间与 token 预算前置检查。
# 边界: 不访问真实模型代理、不执行 FastAPI 请求、不实现 AgentRunner。
##################################################################################################

import asyncio

import pytest
from pydantic import ValidationError

from veterinary_agent.checkpoint_store import CheckpointStoreSettings
from veterinary_agent.config import (
    DEFAULT_LLM_GATEWAY_CONFIG_PATH,
    LlmGatewaySettings,
    LlmModelCapabilityConfig,
    LlmModelProfileConfig,
    LlmProviderRouteConfig,
    LlmRequiredCapabilityConfig,
    RuntimeConfigNamespace,
    create_runtime_config_provider,
    load_llm_gateway_settings,
)
from veterinary_agent.llm_gateway import (
    LlmGatewayError,
    LlmGatewayErrorCode,
    create_default_llm_gateway,
)

from . import (
    FakeProviderAdapter,
    build_invocation_request,
    build_success_response,
    build_test_settings,
)


def test_load_llm_gateway_settings_from_default_yaml() -> None:
    """验证 LlmGateway 默认配置可加载且默认关闭真实模型调用。

    :return: None。
    """

    settings = load_llm_gateway_settings()

    assert DEFAULT_LLM_GATEWAY_CONFIG_PATH.name == "llm_gateway.yaml"
    assert settings.enabled is False
    assert settings.provider_routes == []
    assert settings.model_profiles == []


def test_llm_gateway_settings_rejects_enabled_without_routes() -> None:
    """验证启用 LlmGateway 时必须声明路由和 profile。

    :return: None。
    """

    with pytest.raises(ValidationError, match="provider route"):
        LlmGatewaySettings(enabled=True)


def test_llm_gateway_settings_rejects_capability_mismatch() -> None:
    """验证配置期会拒绝 profile 要求能力超过路由能力。

    :return: None。
    """

    route = LlmProviderRouteConfig(
        provider_route_id="route_1",
        provider_name="test-provider",
        base_url="http://proxy.test",
        model_alias="model_1",
        capability=LlmModelCapabilityConfig(supports_tools=False),
    )
    profile = LlmModelProfileConfig(
        model_profile_id="profile_1",
        profile_version="v1",
        provider_route_id="route_1",
        required_capability=LlmRequiredCapabilityConfig(tools=True),
    )

    with pytest.raises(ValidationError, match="路由能力不满足"):
        LlmGatewaySettings(
            enabled=True,
            provider_routes=[route],
            model_profiles=[profile],
        )


def test_runtime_config_snapshot_exposes_llm_gateway_namespace() -> None:
    """验证 RuntimeConfig 快照包含 LlmGateway 命名空间和 trace-safe 摘要。

    :return: None。
    """

    settings = build_test_settings()
    provider = create_runtime_config_provider(
        checkpoint_store_settings=CheckpointStoreSettings(),
        llm_gateway_settings=settings,
    )

    assert provider.get_namespace(RuntimeConfigNamespace.LLM_GATEWAY) is (
        provider.current_snapshot().llm_gateway
    )
    summary = provider.trace_safe_summary()["llm_gateway"]
    assert isinstance(summary, dict)
    assert summary["enabled"] is True
    assert "provider_routes" in summary
    assert "base_url" not in str(summary)
    assert "api_key_env" not in str(summary)


def test_estimate_tokens_and_context_length_precheck() -> None:
    """验证 LlmGateway 估算 token 并在上下文超限时快速失败。

    :return: None。
    """

    settings = build_test_settings(max_context_tokens=256)
    adapter = FakeProviderAdapter(response=build_success_response())
    gateway = create_default_llm_gateway(
        settings=settings,
        adapters_by_profile={"profile_primary": adapter},
    )
    request = build_invocation_request(content="猫咪呕吐")

    estimate = gateway.estimate_tokens(request)
    assert estimate.model_profile_id == "profile_primary"
    assert estimate.total_budget_tokens <= estimate.max_context_tokens

    oversized = build_invocation_request(content="x" * 1000)
    oversized_estimate = gateway.estimate_tokens(oversized)
    assert oversized_estimate.total_budget_tokens > (
        oversized_estimate.max_context_tokens
    )
    with pytest.raises(LlmGatewayError) as exc_info:
        asyncio.run(gateway.invoke(oversized))

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED
