"""
文件：src/vet_agent/memory/models.py
作用：定义记忆读取数据链的结构化领域模型。
范围：承载 PostgreSQL 权威事实、当前会话滑动窗口、宠物中期 episode、Mem0 语义投影召回、问诊状态快照与读取审计摘要。
说明：本文件不访问数据库、不调用外部服务，仅定义跨数据链传递的稳定结构；跨包调用应通过 vet_agent.memory 顶层导出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from vet_agent import Evidence


@dataclass(frozen=True)
class AuthoritativeMemoryFact:
    """表示 PostgreSQL 长期事实库中的权威记忆事实。

    :param fact_type: 事实类型。
    :param fact_key: 事实键名。
    :param fact_value: 事实内容。
    :param confidence: 事实置信度。
    :param source_turn_id: 事实来源回合标识。
    :param source_text: 事实来源文本。
    :param metadata: 事实附加审计信息。
    :param updated_at: 事实最近更新时间。
    :return: 无返回值。
    """

    fact_type: str
    fact_key: str
    fact_value: str
    confidence: float
    source_turn_id: str | None = None
    source_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为旧版记忆管理接口兼容字典。

        :return: 返回旧版 pet.facts 条目结构。
        """
        return {
            "fact_type": self.fact_type,
            "fact_key": self.fact_key,
            "fact_value": self.fact_value,
            "confidence": self.confidence,
            "source_turn_id": self.source_turn_id,
            "source_text": self.source_text,
            "metadata": self.metadata,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class SessionMemoryTurn:
    """表示当前 session 滑动窗口内的最近对话回合。

    :param turn_id: Agent 回合标识。
    :param request_id: 入口请求标识。
    :param trace_id: 链路追踪标识。
    :param user_text: 用户原始输入摘要。
    :param summary: Agent 响应摘要。
    :param medical: 是否属于医疗咨询回合。
    :param metadata: 回合附加审计信息。
    :param created_at: 回合创建时间。
    :return: 无返回值。
    """

    turn_id: str | None
    request_id: str | None
    trace_id: str | None
    user_text: str
    summary: str
    medical: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为旧版记忆管理接口兼容字典。

        :return: 返回旧版 turns 条目结构。
        """
        return {
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_text": self.user_text,
            "summary": self.summary,
            "medical": self.medical,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class PetMemoryEpisode:
    """表示同一宠物跨 session 的中期历史事件摘要。

    :param title: episode 标题。
    :param summary: episode 摘要。
    :param memory_scope: episode 记忆范围。
    :param metadata: episode 附加审计信息。
    :param created_at: episode 创建时间。
    :return: 无返回值。
    """

    title: str
    summary: str
    memory_scope: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为旧版记忆管理接口兼容字典。

        :return: 返回旧版 pet.episodes 条目结构。
        """
        return {
            "title": self.title,
            "summary": self.summary,
            "memory_scope": self.memory_scope,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class SemanticRecollection:
    """表示 Mem0 根据本轮输入召回的语义记忆投影。

    :param memory_id: Mem0 返回的记忆标识。
    :param content: 语义记忆内容。
    :param score: 召回相关性分数。
    :param metadata: Mem0 记忆附加元数据。
    :param created_at: Mem0 记忆创建时间文本。
    :return: 无返回值。
    """

    memory_id: str | None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为旧版记忆管理接口兼容字典。

        :return: 返回旧版 semantic_memories 条目结构。
        """
        return {
            "id": self.memory_id,
            "memory": self.content,
            "score": self.score,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MemoryReadAudit:
    """表示一次记忆读取链路的审计摘要。

    :param purpose: 读取目的。
    :param source: 读取链路来源描述。
    :param facts_count: 权威事实数量。
    :param session_turns_count: 当前 session 滑动窗口回合数量。
    :param pet_episodes_count: 宠物中期 episode 数量。
    :param semantic_recollections_count: Mem0 语义召回数量。
    :param semantic_status: Mem0 语义投影读取状态。
    :param degraded: 读取链路是否存在显式降级。
    :param details: 附加审计字段。
    :return: 无返回值。
    """

    purpose: Literal["agent_turn", "management_snapshot"]
    source: str
    facts_count: int
    session_turns_count: int
    pet_episodes_count: int
    semantic_recollections_count: int
    semantic_status: Literal["queried", "empty", "disabled", "skipped", "degraded"]
    degraded: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可安全记录的审计摘要。

        :return: 返回记忆读取审计 metadata。
        """
        return {
            "purpose": self.purpose,
            "source": self.source,
            "facts_count": self.facts_count,
            "session_turns_count": self.session_turns_count,
            "pet_episodes_count": self.pet_episodes_count,
            "semantic_recollections_count": self.semantic_recollections_count,
            "semantic_status": self.semantic_status,
            "degraded": self.degraded,
            "details": self.details,
        }


@dataclass(frozen=True)
class MemoryReadBundle:
    """表示 Agent 主链路统一消费的结构化记忆读取结果。

    :param authoritative_facts: PostgreSQL 权威长期事实。
    :param recent_session_turns: 当前 session 滑动窗口回合。
    :param recent_pet_episodes: 当前宠物中期历史 episode。
    :param semantic_recollections: Mem0 语义投影召回结果。
    :param consultation_state: 当前 session 默认问诊状态。
    :param task_consultation_states: 当前 session 多任务问诊状态集合。
    :param audit: 记忆读取链路审计摘要。
    :return: 无返回值。
    """

    authoritative_facts: tuple[AuthoritativeMemoryFact, ...] = field(default_factory=tuple)
    recent_session_turns: tuple[SessionMemoryTurn, ...] = field(default_factory=tuple)
    recent_pet_episodes: tuple[PetMemoryEpisode, ...] = field(default_factory=tuple)
    semantic_recollections: tuple[SemanticRecollection, ...] = field(default_factory=tuple)
    consultation_state: dict[str, Any] = field(default_factory=dict)
    task_consultation_states: dict[str, Any] = field(default_factory=dict)
    audit: MemoryReadAudit | None = None

    def last_summary(self) -> str:
        """读取当前 session 最近一轮摘要。

        :return: 存在最近回合时返回其摘要，否则返回空字符串。
        """
        return self.recent_session_turns[0].summary if self.recent_session_turns else ""

    def to_metadata(self) -> dict[str, Any]:
        """转换为主业务响应 metadata 中的记忆读取摘要。

        :return: 返回记忆读取 metadata。
        """
        audit = self.audit.to_metadata() if self.audit is not None else {}
        return {
            "audit": audit,
            "has_consultation_state": bool(self.consultation_state),
            "task_consultation_state_count": len(self.task_consultation_states),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """转换为旧版记忆管理接口兼容响应结构。

        :return: 返回旧版 owner、pet、session 三段式记忆结构。
        """
        last_summary = self.last_summary()
        pet_memory: dict[str, Any] = {}
        if any(
            [
                last_summary,
                self.recent_session_turns,
                self.authoritative_facts,
                self.recent_pet_episodes,
                self.semantic_recollections,
            ]
        ):
            pet_memory = {
                "last_summary": last_summary,
                "turns": [turn.to_legacy_dict() for turn in self.recent_session_turns],
                "facts": [fact.to_legacy_dict() for fact in self.authoritative_facts],
                "episodes": [episode.to_legacy_dict() for episode in self.recent_pet_episodes],
                "semantic_memories": [item.to_legacy_dict() for item in self.semantic_recollections],
            }
        return {
            "owner": {},
            "pet": pet_memory,
            "session": {
                "last_summary": last_summary,
                "turns": [turn.to_legacy_dict() for turn in self.recent_session_turns],
                "consultation_state": self.consultation_state,
                "task_consultation_states": self.task_consultation_states,
            },
        }


@dataclass(frozen=True)
class MemoryPromptContext:
    """表示回复生成 Agent 可消费的记忆提示词上下文。

    :param prompt_text: 已分层编译后的记忆提示词。
    :param evidence: 记忆读取产生的可展示证据。
    :param metadata: 记忆上下文编译审计摘要。
    :return: 无返回值。
    """

    prompt_text: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
