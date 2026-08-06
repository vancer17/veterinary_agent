"""
文件：src/vet_agent/services/semantic_memory.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Any

import httpx

from vet_agent import Settings
from vet_agent import TrustedIdentity


class DisabledSemanticMemory:
    enabled = False

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """执行 add_turn 业务逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        return None

    async def search(self, identity: TrustedIdentity, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """执行 search 业务逻辑。

        :param identity: 可信身份信息。
        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        return []

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """执行 delete_pet 业务逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        return None


class Mem0RestSemanticMemory:
    """Real Mem0 middleware client using the self-hosted REST API."""

    enabled = True

    def __init__(self, *, base_url: str, api_key: str | None, timeout_seconds: float) -> None:
        """初始化当前对象。

        :param base_url: 参数 base_url。
        :param api_key: 参数 api_key。
        :param timeout_seconds: 参数 timeout_seconds。
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
        """执行 add_turn 业务逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
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

    async def search(self, identity: TrustedIdentity, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """执行 search 业务逻辑。

        :param identity: 可信身份信息。
        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        if not query.strip():
            return []
        payload = {
            "query": query[:2000],
            "filters": {"user_id": identity.user_id, "run_id": identity.pet_id},
            "top_k": limit,
        }
        data = await self._request("POST", "/search", json=payload)
        if isinstance(data, dict):
            memories = data.get("results") or data.get("memories") or data.get("data") or []
        else:
            memories = data or []
        return [item for item in memories if isinstance(item, dict)]

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """执行 delete_pet 业务逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
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
        """执行 _request 内部辅助逻辑。

        :param method: 参数 method。
        :param path: 文件或接口路径。
        :param json: 参数 json。
        :param params: 参数 params。
        :return: 返回函数执行结果。
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


def make_semantic_memory(settings: Settings):
    """执行 make_semantic_memory 业务逻辑。

    :param settings: 应用配置对象。
    :return: 返回函数执行结果。
    """
    if not settings.enable_mem0:
        return DisabledSemanticMemory()
    return Mem0RestSemanticMemory(
        base_url=settings.mem0_base_url,
        api_key=settings.mem0_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
