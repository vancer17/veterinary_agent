"""
文件：src/vet_agent/services/access_control.py
作用：提供外部入口认证门面，并将身份、宠物资料与会话范围授权委托给 ScopeContextService。
范围：位于 HTTP 入口与 Agent 主链路之间，仅处理 API 凭据解析、认证主体一致性和范围授权调度。
说明：本文件不直接访问数据库模型，不根据请求侧 pet_info 建立权威宠物资料，也不保留 JSON 回退授权路径。
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any

from ingress import UnauthorizedError
from vet_agent import ScopeAssertion, Settings, TrustedIdentity

from .scope import AuthenticatedPrincipal, ScopeContext, ScopeContextService


class AccessControlService:
    """提供入口认证与范围授权统一门面。

    :return: 无返回值。
    """

    def __init__(self, settings: Settings, scope_service: ScopeContextService) -> None:
        """初始化访问控制服务。

        :param settings: 应用配置对象。
        :param scope_service: 身份、宠物资料与会话范围上下文服务。
        :return: 无返回值。
        """
        self.settings = settings
        self.scope_service = scope_service

    def authenticate(self, headers: Mapping[str, str]) -> AuthenticatedPrincipal:
        """解析并校验调用方认证信息。

        :param headers: HTTP 请求头。
        :return: 返回认证主体投影。
        """
        configured_keys = self.settings.api_keys
        auth_required = self.settings.require_api_auth or bool(configured_keys)
        if not auth_required:
            return AuthenticatedPrincipal(authenticated=False)

        token = self._bearer_token(headers) or self._header_value(headers, "x-api-key")
        if not token:
            raise UnauthorizedError("Missing API credential")
        if configured_keys and not any(hmac.compare_digest(token, item) for item in configured_keys):
            raise UnauthorizedError("Invalid API credential")
        return AuthenticatedPrincipal(
            api_key_id=self._fingerprint(token),
            user_id=self._header_value(headers, "x-user-id"),
            authenticated=True,
        )

    async def authorize(
        self,
        scope_assertion: ScopeAssertion,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> ScopeContext:
        """执行本轮请求的身份、宠物资料与会话范围授权。

        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :param pet_info: 请求侧未验证宠物资料，仅作为审计副本进入范围上下文。
        :param principal: 上游认证主体。
        :return: 返回已通过裁决的范围上下文。
        """
        return await self.scope_service.authorize(
            scope_assertion,
            pet_info=pet_info,
            principal=principal,
        )

    async def authorize_identity(
        self,
        identity: TrustedIdentity,
        *,
        pet_info: dict[str, Any] | None = None,
        principal: AuthenticatedPrincipal | None = None,
    ) -> ScopeContext:
        """基于既有本地画像投影执行管理接口范围授权。

        :param identity: 本轮可信身份范围。
        :param pet_info: 请求侧未验证宠物资料，仅作为审计副本进入范围上下文。
        :param principal: 上游认证主体。
        :return: 返回已通过裁决的范围上下文。
        """
        return await self.scope_service.authorize_identity(
            identity,
            pet_info=pet_info,
            principal=principal,
        )

    def is_ready(self) -> bool:
        """检查访问控制依赖的范围上下文服务是否就绪。

        :return: 范围上下文服务就绪时返回 True。
        """
        return self.scope_service.is_ready()

    def _bearer_token(self, headers: Mapping[str, str]) -> str | None:
        """从 Authorization 请求头提取 Bearer Token。

        :param headers: HTTP 请求头。
        :return: 存在 Bearer Token 时返回令牌文本，否则返回 None。
        """
        value = self._header_value(headers, "authorization")
        if not value:
            return None
        prefix = "bearer "
        if value.lower().startswith(prefix):
            token = value[len(prefix) :].strip()
            return token or None
        return None

    def _header_value(self, headers: Mapping[str, str], name: str) -> str | None:
        """按大小写兼容方式读取请求头。

        :param headers: HTTP 请求头。
        :param name: 请求头名称。
        :return: 存在非空请求头时返回其文本，否则返回 None。
        """
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _fingerprint(self, token: str) -> str:
        """生成用于审计的令牌脱敏指纹。

        :param token: 原始令牌。
        :return: 返回脱敏后的令牌指纹。
        """
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}...{token[-4:]}"
