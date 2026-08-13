"""
文件：src/vet_agent/memory/read_service.py
作用：编排 Agent 主链路的结构化记忆读取流程。
范围：聚合 PostgreSQL 权威事实、当前 session 滑动窗口、宠物 episode、问诊状态和 Mem0 语义投影召回。
说明：本服务只做读取编排、依赖失败语义和审计汇总，不实现关键词、症状分类或临床状态机。
"""

from __future__ import annotations

from vet_agent import Settings, TrustedIdentity

from .errors import MemoryProjectionClientError, MemoryReadDependencyError
from .models import MemoryReadAudit, MemoryReadBundle, SemanticRecollection
from .ports import MemoryProjectionClient, MemoryReadRepository


class MemoryReadService:
    """提供 Agent 主链路使用的结构化记忆读取服务。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        repository: MemoryReadRepository,
        projection_client: MemoryProjectionClient,
    ) -> None:
        """初始化结构化记忆读取服务。

        :param settings: 当前运行环境配置。
        :param repository: 权威记忆读取仓储。
        :param projection_client: Mem0 语义记忆投影客户端。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self.projection_client = projection_client

    async def read(
        self,
        identity: TrustedIdentity,
        *,
        current_user_text: str,
        pet_context_summary: str = "",
    ) -> MemoryReadBundle:
        """读取 Agent 当前回合所需的结构化记忆上下文。

        :param identity: 本轮可信身份范围。
        :param current_user_text: 当前回合用户输入；用于 Mem0 语义召回，禁止使用硬编码兜底查询。
        :param pet_context_summary: 已验证宠物上下文摘要，仅进入审计，不驱动规则分支。
        :return: 返回结构化记忆读取结果。
        """
        del pet_context_summary
        query = current_user_text.strip()
        if not query:
            raise MemoryReadDependencyError(
                "current user text is required for memory read",
                details={"reason": "empty_current_user_text"},
            )
        return await self._read_bundle(identity, purpose="agent_turn", semantic_query=query)

    async def read_snapshot(self, identity: TrustedIdentity) -> MemoryReadBundle:
        """读取记忆管理接口使用的结构化快照。

        :param identity: 本轮可信身份范围。
        :return: 返回不触发 Mem0 语义查询的结构化记忆快照。
        """
        return await self._read_bundle(identity, purpose="management_snapshot", semantic_query=None)

    def is_ready(self) -> bool:
        """检查结构化记忆读取链路是否可用。

        :return: 权威仓储和语义投影客户端配置均就绪时返回 True。
        """
        return self.repository.is_ready() and self.projection_client.is_ready()

    async def _read_bundle(
        self,
        identity: TrustedIdentity,
        *,
        purpose: str,
        semantic_query: str | None,
    ) -> MemoryReadBundle:
        """读取结构化记忆各分层并汇总审计信息。

        :param identity: 本轮可信身份范围。
        :param purpose: 读取目的。
        :param semantic_query: Mem0 语义查询文本；为空时跳过语义投影查询。
        :return: 返回结构化记忆读取结果。
        """
        try:
            facts = self.repository.read_authoritative_facts(identity)
            turns = self.repository.read_recent_session_turns(
                identity,
                limit=self.settings.memory_read_session_turn_limit,
            )
            episodes = self.repository.read_recent_pet_episodes(
                identity,
                limit=self.settings.memory_read_pet_episode_limit,
            )
            consultation_state = self.repository.read_default_consultation_state(identity)
            task_states = self.repository.read_task_consultation_states(identity)
        except Exception as exc:
            raise MemoryReadDependencyError(
                "authoritative memory repository is unavailable",
                details={"error_type": type(exc).__name__},
            ) from exc

        semantic_status = "skipped"
        semantic_recollections: tuple[SemanticRecollection, ...] = ()
        degraded = False
        if purpose == "agent_turn" and semantic_query is not None:
            semantic_recollections, semantic_status, degraded = await self._semantic_recollections(
                identity,
                semantic_query,
            )
        elif not self.projection_client.enabled:
            semantic_status = "disabled"

        audit = MemoryReadAudit(
            purpose="agent_turn" if purpose == "agent_turn" else "management_snapshot",
            source="postgres_authoritative_memory_with_mem0_projection",
            facts_count=len(facts),
            session_turns_count=len(turns),
            pet_episodes_count=len(episodes),
            semantic_recollections_count=len(semantic_recollections),
            semantic_status=semantic_status,
            degraded=degraded,
            details={
                "session_turn_limit": self.settings.memory_read_session_turn_limit,
                "pet_episode_limit": self.settings.memory_read_pet_episode_limit,
                "semantic_limit": self.settings.memory_read_semantic_limit,
                "mem0_enabled": self.projection_client.enabled,
            },
        )
        return MemoryReadBundle(
            authoritative_facts=facts,
            recent_session_turns=turns,
            recent_pet_episodes=episodes,
            semantic_recollections=semantic_recollections,
            consultation_state=consultation_state,
            task_consultation_states=task_states,
            audit=audit,
        )

    async def _semantic_recollections(
        self,
        identity: TrustedIdentity,
        query: str,
    ) -> tuple[tuple[SemanticRecollection, ...], str, bool]:
        """读取 Mem0 语义记忆投影并处理显式失败语义。

        :param identity: 本轮可信身份范围。
        :param query: 当前回合用户输入。
        :return: 返回语义召回结果、状态和是否降级。
        """
        if not self.projection_client.enabled:
            return (), "disabled", False
        try:
            recollections = await self.projection_client.search(
                identity,
                query,
                limit=self.settings.memory_read_semantic_limit,
            )
        except MemoryProjectionClientError as exc:
            if self.settings.memory_read_allow_semantic_degraded:
                return (), "degraded", True
            raise MemoryReadDependencyError(
                "semantic memory projection is unavailable",
                details={"error_type": type(exc).__name__, **exc.details},
            ) from exc
        return recollections, "queried" if recollections else "empty", False
