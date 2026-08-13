"""
文件：src/vet_agent/services/semantic_memory.py
作用：封装记忆写入阶段的 Mem0 语义投影同步客户端。
范围：仅负责把已完成回合写入 Mem0 投影以及按宠物范围删除投影；结构化记忆读取统一由 vet_agent.memory 数据链负责。
说明：本文件不提供记忆读取入口，避免 Mem0 读路径在 services 与 memory 两处并存；跨包调用应通过 vet_agent.services 顶层导出。
"""


from __future__ import annotations

from typing import Any, Protocol

import httpx

from vet_agent import Settings
from vet_agent import TrustedIdentity


class SemanticMemoryWriter(Protocol):
    """定义记忆写入阶段依赖的语义投影同步协议。

    :return: 无返回值。
    """

    @property
    def enabled(self) -> bool:
        """读取语义投影同步是否启用。

        :return: 启用 Mem0 语义投影同步时返回 True。
        """
        ...

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """将已完成回合写入语义记忆投影。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: Agent 响应摘要。
        :param metadata: 附加元数据。
        :return: 无返回值。
        """
        ...

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """删除指定宠物范围内的语义记忆投影。

        :param pet_id: 宠物标识。
        :param user_id: 用户标识；存在时限定用户范围。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查语义投影同步客户端配置是否可用。

        :return: 配置完整或显式禁用时返回 True。
        """
        ...


class DisabledSemanticMemory(SemanticMemoryWriter):
    """表示显式禁用的 Mem0 语义投影写入客户端。

    :return: 无返回值。
    """

    enabled = False

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """在显式禁用 Mem0 写入时跳过语义投影同步。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: Agent 响应摘要。
        :param metadata: 附加元数据。
        :return: 无返回值。
        """
        del identity, user_text, summary, metadata
        return None

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """在显式禁用 Mem0 写入时跳过语义投影删除。

        :param pet_id: 宠物标识。
        :param user_id: 用户标识；存在时限定用户范围。
        :return: 无返回值。
        """
        del pet_id, user_id
        return None

    def is_ready(self) -> bool:
        """检查禁用态语义投影写入客户端是否可用于装配。

        :return: 始终返回 True，表示禁用为显式配置状态。
        """
        return True


class Mem0RestSemanticMemory(SemanticMemoryWriter):
    """基于自托管 Mem0 REST API 的语义投影写入客户端。

    :return: 无返回值。
    """

    enabled = True

    def __init__(self, *, base_url: str, api_key: str | None, timeout_seconds: float) -> None:
        """初始化 Mem0 语义投影写入客户端。

        :param base_url: Mem0 REST API 基础地址。
        :param api_key: Mem0 API 鉴权密钥。
        :param timeout_seconds: HTTP 请求超时时间。
        :return: 无返回值。
        """
        if not base_url:
            raise ValueError("MEM0_BASE_URL is required when ENABLE_MEM0=true")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """将已完成回合写入 Mem0 语义记忆投影。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: Agent 响应摘要。
        :param metadata: 附加元数据。
        :return: 无返回值。
        """
        payload_metadata = {
            "user_id": identity.user_id,
            "pet_id": identity.pet_id,
            "session_id": identity.session_id,
            "memory_scope": "semantic",
            **(metadata or {}),
        }
        payload = {
            "messages": [
                {"role": "user", "content": user_text[:4000]},
                {"role": "assistant", "content": summary[:4000]},
            ],
            "user_id": identity.user_id,
            "run_id": identity.pet_id,
            "metadata": payload_metadata,
        }
        await self._request("POST", "/memories", json=payload)

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """删除指定宠物范围内的 Mem0 语义记忆投影。

        :param pet_id: 宠物标识。
        :param user_id: 用户标识；存在时限定用户范围。
        :return: 无返回值。
        """
        params = {"run_id": pet_id}
        if user_id:
            params["user_id"] = user_id
        await self._request("DELETE", "/memories", params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """调用 Mem0 REST API 并返回 JSON 响应。

        :param method: HTTP 方法。
        :param path: API 路径。
        :param json: JSON 请求体。
        :param params: 查询参数。
        :return: 返回 Mem0 JSON 响应。
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()

    def is_ready(self) -> bool:
        """检查 Mem0 语义投影写入配置是否完整。

        :return: 基础地址存在时返回 True。
        """
        return bool(self.base_url)


def make_semantic_memory(settings: Settings) -> SemanticMemoryWriter:
    """根据应用配置构造记忆写入使用的语义投影同步客户端。

    :param settings: 应用配置对象。
    :return: 返回 Mem0 或禁用态语义投影写入客户端。
    """
    if not settings.enable_mem0:
        return DisabledSemanticMemory()
    return Mem0RestSemanticMemory(
        base_url=settings.mem0_base_url,
        api_key=settings.mem0_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
