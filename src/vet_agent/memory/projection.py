"""
文件：src/vet_agent/memory/projection.py
作用：封装 Mem0 REST 语义记忆投影读取客户端。
范围：仅负责按当前回合输入查询 Mem0、校验返回结构与用户宠物范围，并输出结构化 SemanticRecollection。
说明：本文件不写入 PostgreSQL 权威事实，不参与临床安全裁决，不实现关键词或症状规则状态机。
"""

from __future__ import annotations

from typing import Any

import httpx

from vet_agent import Settings, TrustedIdentity

from .errors import MemoryProjectionClientError, MemoryProjectionScopeError
from .models import SemanticRecollection
from .ports import MemoryProjectionClient


class DisabledMemoryProjectionClient(MemoryProjectionClient):
    """表示显式禁用的 Mem0 语义投影客户端。

    :return: 无返回值。
    """

    enabled = False

    async def search(
        self,
        identity: TrustedIdentity,
        query: str,
        *,
        limit: int,
    ) -> tuple[SemanticRecollection, ...]:
        """在显式禁用 Mem0 时返回空语义召回。

        :param identity: 本轮可信身份范围。
        :param query: 当前用户输入或结构化查询文本。
        :param limit: 召回数量上限。
        :return: 始终返回空元组。
        """
        del identity, query, limit
        return ()

    def is_ready(self) -> bool:
        """检查禁用态语义投影客户端是否可用于装配。

        :return: 始终返回 True，表示禁用为显式配置状态。
        """
        return True


class Mem0MemoryProjectionClient(MemoryProjectionClient):
    """基于 Mem0 REST API 的语义记忆投影读取客户端。

    :return: 无返回值。
    """

    enabled = True

    def __init__(self, *, base_url: str, api_key: str | None, timeout_seconds: float) -> None:
        """初始化 Mem0 语义投影读取客户端。

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

    async def search(
        self,
        identity: TrustedIdentity,
        query: str,
        *,
        limit: int,
    ) -> tuple[SemanticRecollection, ...]:
        """按当前回合输入查询 Mem0 语义记忆投影。

        :param identity: 本轮可信身份范围。
        :param query: 当前用户输入文本；不得使用硬编码兜底查询。
        :param limit: 召回数量上限。
        :return: 返回经范围校验后的语义召回结果。
        """
        if not query.strip():
            raise MemoryProjectionClientError(
                "semantic memory query is required",
                details={"reason": "empty_query"},
            )
        payload = {
            "query": query[:2000],
            "filters": {"user_id": identity.user_id, "run_id": identity.pet_id},
            "top_k": limit,
        }
        data = await self._request("POST", "/search", json=payload)
        raw_items = self._items(data)
        recollections = [
            self._recollection(identity, item)
            for item in raw_items
        ]
        return tuple(item for item in recollections if item.content.strip())

    def is_ready(self) -> bool:
        """检查 Mem0 语义投影读取配置是否完整。

        :return: 基础地址存在时返回 True。
        """
        return bool(self.base_url)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """调用 Mem0 REST API 并返回 JSON 响应。

        :param method: HTTP 方法。
        :param path: API 路径。
        :param json: JSON 请求体。
        :return: 返回 Mem0 JSON 响应。
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json,
                )
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
        except httpx.HTTPError as exc:
            raise MemoryProjectionClientError(
                "semantic memory projection request failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        except ValueError as exc:
            raise MemoryProjectionClientError(
                "semantic memory projection returned invalid JSON",
                details={"error_type": type(exc).__name__},
            ) from exc

    def _items(self, data: Any) -> list[dict[str, Any]]:
        """从 Mem0 响应中提取候选记忆列表。

        :param data: Mem0 原始响应。
        :return: 返回原始候选记忆字典列表。
        """
        if data is None:
            return []
        if isinstance(data, dict):
            raw_items = data.get("results") or data.get("memories") or data.get("data") or []
        else:
            raw_items = data
        if not isinstance(raw_items, list):
            raise MemoryProjectionClientError(
                "semantic memory projection returned invalid result list",
                details={"result_type": type(raw_items).__name__},
            )
        return [item for item in raw_items if isinstance(item, dict)]

    def _recollection(self, identity: TrustedIdentity, item: dict[str, Any]) -> SemanticRecollection:
        """将 Mem0 原始结果转换为结构化语义记忆投影。

        :param identity: 本轮可信身份范围。
        :param item: Mem0 原始结果条目。
        :return: 返回结构化语义记忆投影。
        """
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        self._assert_scope(identity, item, metadata)
        content = item.get("memory") or item.get("content") or item.get("text") or item.get("value") or ""
        score = item.get("score")
        return SemanticRecollection(
            memory_id=str(item.get("id")) if item.get("id") is not None else None,
            content=str(content),
            score=float(score) if isinstance(score, int | float) else None,
            metadata=dict(metadata),
            created_at=str(item.get("created_at")) if item.get("created_at") is not None else None,
        )

    def _assert_scope(
        self,
        identity: TrustedIdentity,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """校验 Mem0 结果没有跨用户或跨宠物污染。

        :param identity: 本轮可信身份范围。
        :param item: Mem0 原始结果条目。
        :param metadata: Mem0 结果元数据。
        :return: 无返回值；范围不一致时抛出异常。
        """
        user_id = item.get("user_id") or metadata.get("user_id")
        pet_id = item.get("run_id") or metadata.get("pet_id")
        if user_id != identity.user_id or pet_id != identity.pet_id:
            raise MemoryProjectionScopeError(
                "semantic memory projection scope mismatch",
                details={
                    "expected_user_id": identity.user_id,
                    "expected_pet_id": identity.pet_id,
                    "actual_user_id": user_id,
                    "actual_pet_id": pet_id,
                },
            )


def make_memory_projection_client(settings: Settings) -> MemoryProjectionClient:
    """根据应用配置构造记忆读取使用的语义投影客户端。

    :param settings: 当前运行环境配置。
    :return: 返回 Mem0 或禁用态语义投影客户端。
    """
    if not settings.enable_mem0:
        return DisabledMemoryProjectionClient()
    return Mem0MemoryProjectionClient(
        base_url=settings.mem0_base_url,
        api_key=settings.mem0_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
