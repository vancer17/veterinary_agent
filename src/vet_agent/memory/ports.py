"""
文件：src/vet_agent/memory/ports.py
作用：定义记忆读取领域依赖的数据仓储与语义投影协议。
范围：隔离业务读取编排、PostgreSQL 数据表模型、Mem0 REST 客户端和测试替身。
说明：实现类需显式继承对应 Protocol，业务层只依赖协议，不直接访问数据表模型或外部服务实现。
"""

from __future__ import annotations

from typing import Any, Protocol

from vet_agent import TrustedIdentity

from .models import (
    AuthoritativeMemoryFact,
    PetMemoryEpisode,
    SemanticRecollection,
    SessionMemoryTurn,
)


class MemoryReadRepository(Protocol):
    """定义 PostgreSQL 权威记忆读取仓储协议。

    :return: 无返回值。
    """

    def read_authoritative_facts(self, identity: TrustedIdentity) -> tuple[AuthoritativeMemoryFact, ...]:
        """读取当前用户与宠物范围内的权威长期事实。

        :param identity: 本轮可信身份范围。
        :return: 返回权威长期事实元组。
        """
        ...

    def read_recent_session_turns(self, identity: TrustedIdentity, *, limit: int) -> tuple[SessionMemoryTurn, ...]:
        """读取当前 session 的最近对话滑动窗口。

        :param identity: 本轮可信身份范围。
        :param limit: 读取回合数量上限。
        :return: 返回按创建时间倒序排列的当前 session 回合元组。
        """
        ...

    def read_recent_pet_episodes(self, identity: TrustedIdentity, *, limit: int) -> tuple[PetMemoryEpisode, ...]:
        """读取当前宠物的中期历史 episode。

        :param identity: 本轮可信身份范围。
        :param limit: 读取 episode 数量上限。
        :return: 返回按创建时间倒序排列的宠物 episode 元组。
        """
        ...

    def read_default_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前 session 的默认活跃问诊状态。

        :param identity: 本轮可信身份范围。
        :return: 返回默认问诊状态字典；不存在时返回空字典。
        """
        ...

    def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前 session 的多任务活跃问诊状态集合。

        :param identity: 本轮可信身份范围。
        :return: 返回任务状态键到问诊状态的映射。
        """
        ...

    def is_ready(self) -> bool:
        """检查记忆读取仓储是否可访问。

        :return: 依赖数据表可访问时返回 True。
        """
        ...


class MemoryProjectionClient(Protocol):
    """定义 Mem0 语义记忆投影读取协议。

    :return: 无返回值。
    """

    @property
    def enabled(self) -> bool:
        """读取语义投影客户端是否启用。

        :return: 启用 Mem0 语义投影时返回 True。
        """
        ...

    async def search(
        self,
        identity: TrustedIdentity,
        query: str,
        *,
        limit: int,
    ) -> tuple[SemanticRecollection, ...]:
        """按当前回合语义查询 Mem0 记忆投影。

        :param identity: 本轮可信身份范围。
        :param query: 当前用户输入或结构化查询文本。
        :param limit: 召回数量上限。
        :return: 返回语义记忆召回结果元组。
        """
        ...

    def is_ready(self) -> bool:
        """检查语义投影客户端配置是否可用于读取链路。

        :return: 客户端配置完整或显式禁用时返回 True。
        """
        ...
