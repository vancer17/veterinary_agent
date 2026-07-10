##################################################################################################
# 文件: tests/llm_gateway/test_config_and_registry.py
# 作用: 验证 LlmGateway 配置关系、fallback 图、profile 注册表解析与静态可用状态。
# 边界: 不创建网络客户端；仅使用公开配置模型、注册表和 fake adapter 验证组件控制面契约。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent import (
    LlmGatewayError,
    LlmGatewayErrorCode,
    LlmGatewaySettings,
    LlmModelCapabilityConfig,
    LlmModelProfileConfig,
    LlmProfileRegistry,
    LlmProviderRouteConfig,
    LlmRequiredCapabilityConfig,
    LlmRetryPolicyConfig,
    create_default_llm_gateway,
)

from . import (
    FakeProviderAdapter,
    build_success_response,
    build_test_settings,
)


def _route(
    *,
    route_id: str,
    max_context_tokens: int = 4096,
    supports_tools: bool = True,
) -> LlmProviderRouteConfig:
    """构建配置关系测试使用的供应商路由。

    :param route_id: 路由稳定 ID。
    :param max_context_tokens: 路由声明的最大上下文长度。
    :param supports_tools: 路由是否声明工具调用能力。
    :return: 可用于 LlmGatewaySettings 的路由配置。
    """

    return LlmProviderRouteConfig(
        provider_route_id=route_id,
        provider_name="test-provider",
        base_url="http://proxy.test",
        model_alias=f"model-{route_id}",
        capability=LlmModelCapabilityConfig(
            max_context_tokens=max_context_tokens,
            supports_streaming=True,
            supports_tools=supports_tools,
        ),
    )


def _profile(
    *,
    profile_id: str,
    route_id: str,
    fallback_profile_ids: list[str] | None = None,
    require_tools: bool = False,
) -> LlmModelProfileConfig:
    """构建配置关系测试使用的模型 profile。

    :param profile_id: 模型 profile 稳定 ID。
    :param route_id: profile 引用的路由 ID。
    :param fallback_profile_ids: 可选备用 profile 列表。
    :param require_tools: profile 是否要求工具调用能力。
    :return: 可用于 LlmGatewaySettings 的 profile 配置。
    """

    return LlmModelProfileConfig(
        model_profile_id=profile_id,
        profile_version="v1",
        provider_route_id=route_id,
        required_capability=LlmRequiredCapabilityConfig(
            streaming=True,
            tools=require_tools,
        ),
        fallback_profile_ids=fallback_profile_ids or [],
        reserved_output_tokens=64,
    )


def test_route_requires_api_key_environment_name_when_auth_enabled() -> None:
    """验证要求代理鉴权的路由必须声明令牌环境变量名。

    :return: None。
    """

    with pytest.raises(ValidationError, match="api_key_env"):
        LlmProviderRouteConfig(
            provider_route_id="route_auth",
            provider_name="proxy",
            base_url="http://proxy.test",
            model_alias="model-auth",
            auth_required=True,
        )


def test_settings_reject_duplicate_route_and_profile_ids() -> None:
    """验证配置拒绝重复路由 ID 与重复 profile ID。

    :return: None。
    """

    route = _route(route_id="route_1")
    profile = _profile(profile_id="profile_1", route_id="route_1")

    with pytest.raises(ValidationError, match="重复 provider_route_id"):
        LlmGatewaySettings(
            provider_routes=[route, route],
            model_profiles=[profile],
        )
    with pytest.raises(ValidationError, match="重复 model_profile_id"):
        LlmGatewaySettings(
            provider_routes=[route],
            model_profiles=[profile, profile],
        )


def test_retry_and_fallback_policies_reject_unknown_error_codes() -> None:
    """验证重试与降级策略拒绝拼写错误或未定义的错误码。

    :return: None。
    """

    with pytest.raises(ValidationError, match="未知"):
        LlmRetryPolicyConfig(retryable_error_codes=["LLM_PROVIDER_UNAVAILBLE"])
    with pytest.raises(ValidationError, match="未知"):
        LlmModelProfileConfig(
            model_profile_id="profile_invalid_error",
            profile_version="v1",
            provider_route_id="route_1",
            fallback_on_error_codes=["LLM_PROXY_UNAVAILBLE"],
        )


def test_settings_reject_missing_and_cyclic_fallback_profiles() -> None:
    """验证 fallback 图拒绝缺失引用和循环降级链。

    :return: None。
    """

    primary_route = _route(route_id="route_primary")
    fallback_route = _route(route_id="route_fallback")
    missing_fallback = _profile(
        profile_id="profile_primary",
        route_id="route_primary",
        fallback_profile_ids=["profile_missing"],
    )

    with pytest.raises(ValidationError, match="不存在的备用 profile"):
        LlmGatewaySettings(
            provider_routes=[primary_route],
            model_profiles=[missing_fallback],
        )

    primary = _profile(
        profile_id="profile_primary",
        route_id="route_primary",
        fallback_profile_ids=["profile_fallback"],
    )
    fallback = _profile(
        profile_id="profile_fallback",
        route_id="route_fallback",
        fallback_profile_ids=["profile_primary"],
    )

    with pytest.raises(ValidationError, match="不得形成环"):
        LlmGatewaySettings(
            provider_routes=[primary_route, fallback_route],
            model_profiles=[primary, fallback],
        )


def test_settings_reject_fallback_with_weaker_context_window() -> None:
    """验证备用 profile 的上下文窗口不得弱于来源 profile。

    :return: None。
    """

    primary_route = _route(
        route_id="route_primary",
        max_context_tokens=4096,
    )
    fallback_route = _route(
        route_id="route_fallback",
        max_context_tokens=2048,
    )
    primary = _profile(
        profile_id="profile_primary",
        route_id="route_primary",
        fallback_profile_ids=["profile_fallback"],
    )
    fallback = _profile(
        profile_id="profile_fallback",
        route_id="route_fallback",
    )

    with pytest.raises(ValidationError, match="上下文长度"):
        LlmGatewaySettings(
            provider_routes=[primary_route, fallback_route],
            model_profiles=[primary, fallback],
        )


def test_profile_registry_resolves_profiles_routes_and_fallbacks() -> None:
    """验证注册表通过稳定 ID 解析 profile、路由与直接 fallback。

    :return: None。
    """

    settings = build_test_settings(include_fallback=True)
    registry = LlmProfileRegistry(settings=settings)

    resolved = registry.resolve_profile("profile_primary")

    assert registry.is_ready() is True
    assert resolved.route.model_alias == "test-primary"
    assert registry.resolve_route("route_fallback").model_alias == "test-fallback"
    assert registry.direct_fallback_profiles("profile_primary") == ("profile_fallback",)


def test_profile_registry_reports_missing_profile_with_stable_error() -> None:
    """验证注册表对缺失 profile 返回稳定错误码。

    :return: None。
    """

    registry = LlmProfileRegistry(settings=build_test_settings())

    with pytest.raises(LlmGatewayError) as exc_info:
        registry.resolve_profile("profile_missing")

    assert exc_info.value.code is LlmGatewayErrorCode.LLM_PROFILE_NOT_FOUND


def test_check_model_profile_reports_adapter_readiness() -> None:
    """验证 profile 静态检查区分健康和未就绪适配器。

    :return: None。
    """

    adapter = FakeProviderAdapter(
        response=build_success_response(),
        ready=False,
    )
    gateway = create_default_llm_gateway(
        settings=build_test_settings(),
        adapters_by_profile={"profile_primary": adapter},
    )

    status = gateway.check_model_profile("profile_primary")

    assert gateway.is_ready() is False
    assert status.available is False
    assert status.reason == "adapter_not_ready"
