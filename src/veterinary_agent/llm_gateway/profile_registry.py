##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/profile_registry.py
# 作用: 将已校验 LlmGateway 配置转换为只读模型 profile 与供应商路由索引。
# 边界: 不加载环境变量、不执行网络调用、不决定业务 generation profile 或动态模型选择。
##################################################################################################

from dataclasses import dataclass

from veterinary_agent.config import (
    LlmGatewaySettings,
    LlmModelProfileConfig,
    LlmProviderRouteConfig,
)
from veterinary_agent.llm_gateway.enums import (
    LlmGatewayErrorCode,
    LlmGatewayOperation,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile:
    """已经关联供应商路由的模型 profile。"""

    profile: LlmModelProfileConfig
    route: LlmProviderRouteConfig


class LlmProfileRegistry:
    """LlmGateway 只读 profile 与路由注册表。"""

    def __init__(self, *, settings: LlmGatewaySettings) -> None:
        """初始化 LlmGateway profile 注册表。

        :param settings: 已完成关系校验的 LlmGateway 配置。
        :return: None。
        """

        self._profiles = {
            profile.model_profile_id: profile for profile in settings.model_profiles
        }
        self._routes = {
            route.provider_route_id: route for route in settings.provider_routes
        }

    def is_ready(self) -> bool:
        """判断注册表是否包含可用 profile 和路由。

        :return: 若 profile 与路由索引均非空，则返回 True。
        """

        return bool(self._profiles and self._routes)

    def resolve_profile(
        self,
        model_profile_id: str,
    ) -> ResolvedModelProfile:
        """解析模型 profile 及其供应商路由。

        :param model_profile_id: 调用方指定的模型 profile ID。
        :return: 已关联供应商路由的模型 profile。
        :raises LlmGatewayError: 当 profile 或其路由不存在时抛出。
        """

        profile = self._profiles.get(model_profile_id)
        if profile is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_NOT_FOUND,
                operation=LlmGatewayOperation.CHECK_MODEL_PROFILE,
                message="模型 profile 不存在",
                model_profile_id=model_profile_id,
            )
        route = self._routes.get(profile.provider_route_id)
        if route is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.CHECK_MODEL_PROFILE,
                message="模型 profile 引用的供应商路由不可用",
                model_profile_id=model_profile_id,
                provider_route_id=profile.provider_route_id,
            )
        return ResolvedModelProfile(profile=profile, route=route)

    def resolve_route(
        self,
        provider_route_id: str,
    ) -> LlmProviderRouteConfig:
        """解析指定供应商路由。

        :param provider_route_id: 供应商路由 ID。
        :return: 命中的供应商路由配置。
        :raises LlmGatewayError: 当供应商路由不存在时抛出。
        """

        route = self._routes.get(provider_route_id)
        if route is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.CHECK_PROVIDER_ROUTE_HEALTH,
                message="供应商路由不存在",
                provider_route_id=provider_route_id,
            )
        return route

    def direct_fallback_profiles(
        self,
        model_profile_id: str,
    ) -> tuple[str, ...]:
        """读取指定 profile 直接声明的备用 profile。

        :param model_profile_id: 来源模型 profile ID。
        :return: 按配置顺序排列的直接备用 profile ID。
        :raises LlmGatewayError: 当来源 profile 不存在时抛出。
        """

        resolved = self.resolve_profile(model_profile_id)
        return tuple(resolved.profile.fallback_profile_ids)


__all__: tuple[str, ...] = (
    "LlmProfileRegistry",
    "ResolvedModelProfile",
)
