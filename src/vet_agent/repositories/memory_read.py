"""
文件：src/vet_agent/repositories/memory_read.py
作用：提供结构化记忆读取数据链的仓储契约实现。
范围：PostgreSQL 实现负责读取 conversation_turns、pet_memory_facts、pet_memory_episodes 与 consultation_states；JSON 实现仅用于显式测试或特殊嵌入场景。
说明：仅本文件允许直接访问记忆读取相关 SQLAlchemy 表模型；业务层需通过 MemoryReadRepository 协议读取数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent import TrustedIdentity
from vet_agent.db import (
    ConsultationStateModel,
    ConversationTurnModel,
    PetMemoryEpisodeModel,
    PetMemoryFactModel,
    make_session_factory,
)
from vet_agent.memory import (
    AuthoritativeMemoryFact,
    MemoryReadRepository,
    PetMemoryEpisode,
    SessionMemoryTurn,
)
from vet_agent.stores import JsonDocumentStore


DEFAULT_TASK_KEY = "__default__"


class MemoryReadRepositoryError(RuntimeError):
    """表示记忆读取仓储访问失败。

    :return: 无返回值。
    """


class PostgresMemoryReadRepository(MemoryReadRepository):
    """基于 PostgreSQL 的结构化记忆读取仓储实现。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 记忆读取仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def read_authoritative_facts(self, identity: TrustedIdentity) -> tuple[AuthoritativeMemoryFact, ...]:
        """读取当前用户与宠物范围内的权威长期事实。

        :param identity: 本轮可信身份范围。
        :return: 返回权威长期事实元组。
        """
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(PetMemoryFactModel)
                    .where(
                        PetMemoryFactModel.user_id == identity.user_id,
                        PetMemoryFactModel.pet_id == identity.pet_id,
                        PetMemoryFactModel.is_active.is_(True),
                    )
                    .order_by(PetMemoryFactModel.fact_type, PetMemoryFactModel.fact_key)
                ).all()
        except SQLAlchemyError as exc:
            raise MemoryReadRepositoryError("failed to read authoritative memory facts") from exc
        return tuple(_fact_from_row(row) for row in rows)

    def read_recent_session_turns(self, identity: TrustedIdentity, *, limit: int) -> tuple[SessionMemoryTurn, ...]:
        """读取当前 session 的最近对话滑动窗口。

        :param identity: 本轮可信身份范围。
        :param limit: 读取回合数量上限。
        :return: 返回按创建时间倒序排列的当前 session 回合元组。
        """
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(ConversationTurnModel)
                    .where(
                        ConversationTurnModel.user_id == identity.user_id,
                        ConversationTurnModel.pet_id == identity.pet_id,
                        ConversationTurnModel.session_id == identity.session_id,
                    )
                    .order_by(desc(ConversationTurnModel.created_at))
                    .limit(limit)
                ).all()
        except SQLAlchemyError as exc:
            raise MemoryReadRepositoryError("failed to read recent session turns") from exc
        return tuple(_turn_from_row(row) for row in rows)

    def read_recent_pet_episodes(self, identity: TrustedIdentity, *, limit: int) -> tuple[PetMemoryEpisode, ...]:
        """读取当前宠物的中期历史 episode。

        :param identity: 本轮可信身份范围。
        :param limit: 读取 episode 数量上限。
        :return: 返回按创建时间倒序排列的宠物 episode 元组。
        """
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(PetMemoryEpisodeModel)
                    .where(
                        PetMemoryEpisodeModel.user_id == identity.user_id,
                        PetMemoryEpisodeModel.pet_id == identity.pet_id,
                    )
                    .order_by(desc(PetMemoryEpisodeModel.created_at))
                    .limit(limit)
                ).all()
        except SQLAlchemyError as exc:
            raise MemoryReadRepositoryError("failed to read pet memory episodes") from exc
        return tuple(_episode_from_row(row) for row in rows)

    def read_default_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前 session 的默认活跃问诊状态。

        :param identity: 本轮可信身份范围。
        :return: 返回默认问诊状态字典；不存在时返回空字典。
        """
        try:
            with self.session_factory() as session:
                row = session.scalar(
                    select(ConsultationStateModel).where(
                        ConsultationStateModel.user_id == identity.user_id,
                        ConsultationStateModel.pet_id == identity.pet_id,
                        ConsultationStateModel.session_id == identity.session_id,
                        ConsultationStateModel.task_key == DEFAULT_TASK_KEY,
                    )
                )
        except SQLAlchemyError as exc:
            raise MemoryReadRepositoryError("failed to read default consultation state") from exc
        return dict(row.state) if row is not None else {}

    def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前 session 的多任务活跃问诊状态集合。

        :param identity: 本轮可信身份范围。
        :return: 返回任务状态键到问诊状态的映射。
        """
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(ConsultationStateModel).where(
                        ConsultationStateModel.user_id == identity.user_id,
                        ConsultationStateModel.pet_id == identity.pet_id,
                        ConsultationStateModel.session_id == identity.session_id,
                        ConsultationStateModel.task_key != DEFAULT_TASK_KEY,
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise MemoryReadRepositoryError("failed to read task consultation states") from exc
        return {row.task_key: dict(row.state) for row in rows}

    def is_ready(self) -> bool:
        """检查 PostgreSQL 记忆读取仓储是否可访问。

        :return: 依赖数据表可访问时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(select(ConversationTurnModel.id).limit(1))
            return True
        except SQLAlchemyError:
            return False


class JsonMemoryReadRepository(MemoryReadRepository):
    """基于 JSON 文档的结构化记忆读取仓储实现。

    说明：该实现仅用于显式测试或特殊嵌入场景；生产路径应使用 PostgreSQL 仓储。

    :return: 无返回值。
    """

    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化 JSON 记忆读取仓储。

        :param store: JSON 文档存储。
        :return: 无返回值。
        """
        self.store = store

    def read_authoritative_facts(self, identity: TrustedIdentity) -> tuple[AuthoritativeMemoryFact, ...]:
        """读取 JSON 测试范围内的长期事实。

        :param identity: 本轮可信身份范围。
        :return: 返回长期事实元组。
        """
        pet_memory = self._pet_memory(identity)
        facts = pet_memory.get("facts") or {}
        if isinstance(facts, dict):
            raw_items = list(facts.values())
        elif isinstance(facts, list):
            raw_items = facts
        else:
            raw_items = []
        return tuple(_fact_from_dict(item) for item in raw_items if isinstance(item, dict))

    def read_recent_session_turns(self, identity: TrustedIdentity, *, limit: int) -> tuple[SessionMemoryTurn, ...]:
        """读取 JSON 测试范围内的当前 session 滑动窗口。

        :param identity: 本轮可信身份范围。
        :param limit: 读取回合数量上限。
        :return: 返回按插入顺序倒序排列的回合元组。
        """
        pet_memory = self._pet_memory(identity)
        turns = pet_memory.get("turns") if isinstance(pet_memory, dict) else []
        if not isinstance(turns, list):
            return ()
        return tuple(_turn_from_dict(item) for item in reversed(turns[-limit:]) if isinstance(item, dict))

    def read_recent_pet_episodes(self, identity: TrustedIdentity, *, limit: int) -> tuple[PetMemoryEpisode, ...]:
        """读取 JSON 测试范围内的宠物中期 episode。

        :param identity: 本轮可信身份范围。
        :param limit: 读取 episode 数量上限。
        :return: 返回 episode 元组。
        """
        del limit
        pet_memory = self._pet_memory(identity)
        episodes = pet_memory.get("episodes") if isinstance(pet_memory, dict) else []
        if not isinstance(episodes, list):
            return ()
        return tuple(_episode_from_dict(item) for item in episodes if isinstance(item, dict))

    def read_default_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 测试范围内的默认活跃问诊状态。

        :param identity: 本轮可信身份范围。
        :return: 返回默认问诊状态字典。
        """
        state = self._session_memory(identity).get("consultation_state", {})
        return dict(state) if isinstance(state, dict) else {}

    def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 测试范围内的多任务活跃问诊状态集合。

        :param identity: 本轮可信身份范围。
        :return: 返回任务状态映射。
        """
        states = self._session_memory(identity).get("task_consultation_states", {})
        return dict(states) if isinstance(states, dict) else {}

    def is_ready(self) -> bool:
        """检查 JSON 测试记忆读取仓储是否可访问。

        :return: 始终返回 True。
        """
        return True

    def _pet_memory(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 文档中的宠物记忆节点。

        :param identity: 本轮可信身份范围。
        :return: 返回宠物记忆字典。
        """
        data = self.store.load()
        value = data.get("pets", {}).get(identity.pet_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def _session_memory(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 文档中的会话记忆节点。

        :param identity: 本轮可信身份范围。
        :return: 返回会话记忆字典。
        """
        data = self.store.load()
        value = data.get("sessions", {}).get(identity.session_id, {})
        return dict(value) if isinstance(value, dict) else {}


def _fact_from_row(row: PetMemoryFactModel) -> AuthoritativeMemoryFact:
    """将长期事实数据表行转换为结构化记忆事实。

    :param row: 长期事实数据表行。
    :return: 返回结构化权威事实。
    """
    return AuthoritativeMemoryFact(
        fact_type=row.fact_type,
        fact_key=row.fact_key,
        fact_value=row.fact_value,
        confidence=row.confidence,
        source_turn_id=row.source_turn_id,
        source_text=row.source_text,
        metadata=row.metadata_json or {},
        updated_at=row.updated_at,
    )


def _turn_from_row(row: ConversationTurnModel) -> SessionMemoryTurn:
    """将会话回合数据表行转换为结构化滑动窗口回合。

    :param row: 会话回合数据表行。
    :return: 返回结构化会话回合。
    """
    return SessionMemoryTurn(
        turn_id=row.turn_id,
        request_id=row.request_id,
        trace_id=row.trace_id,
        user_text=row.input_text,
        summary=row.summary,
        medical=row.medical,
        metadata=row.metadata_json or {},
        created_at=row.created_at,
    )


def _episode_from_row(row: PetMemoryEpisodeModel) -> PetMemoryEpisode:
    """将宠物 episode 数据表行转换为结构化中期历史事件。

    :param row: 宠物 episode 数据表行。
    :return: 返回结构化宠物 episode。
    """
    return PetMemoryEpisode(
        title=row.title,
        summary=row.summary,
        memory_scope=row.memory_scope,
        metadata=row.metadata_json or {},
        created_at=row.created_at,
    )


def _fact_from_dict(item: dict[str, Any]) -> AuthoritativeMemoryFact:
    """将 JSON 事实字典转换为结构化记忆事实。

    :param item: JSON 事实字典。
    :return: 返回结构化权威事实。
    """
    return AuthoritativeMemoryFact(
        fact_type=str(item.get("fact_type") or "unknown"),
        fact_key=str(item.get("fact_key") or "unknown"),
        fact_value=str(item.get("fact_value") or ""),
        confidence=float(item.get("confidence") or 0.0),
        source_turn_id=str(item.get("source_turn_id")) if item.get("source_turn_id") else None,
        source_text=str(item.get("source_text")) if item.get("source_text") else None,
        metadata=dict(item.get("metadata") or {}),
        updated_at=_parse_datetime(item.get("updated_at")),
    )


def _turn_from_dict(item: dict[str, Any]) -> SessionMemoryTurn:
    """将 JSON 回合字典转换为结构化滑动窗口回合。

    :param item: JSON 回合字典。
    :return: 返回结构化会话回合。
    """
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return SessionMemoryTurn(
        turn_id=str(metadata.get("turn_id")) if metadata.get("turn_id") else None,
        request_id=str(metadata.get("request_id")) if metadata.get("request_id") else None,
        trace_id=str(metadata.get("trace_id")) if metadata.get("trace_id") else None,
        user_text=str(item.get("user_text") or ""),
        summary=str(item.get("summary") or ""),
        medical=bool(item.get("medical")),
        metadata=dict(metadata),
        created_at=_parse_datetime(item.get("at") or item.get("created_at")),
    )


def _episode_from_dict(item: dict[str, Any]) -> PetMemoryEpisode:
    """将 JSON episode 字典转换为结构化宠物历史事件。

    :param item: JSON episode 字典。
    :return: 返回结构化宠物 episode。
    """
    return PetMemoryEpisode(
        title=str(item.get("title") or "历史咨询"),
        summary=str(item.get("summary") or ""),
        memory_scope=str(item.get("memory_scope") or "medium"),
        metadata=dict(item.get("metadata") or {}),
        created_at=_parse_datetime(item.get("created_at")),
    )


def _parse_datetime(value: Any) -> datetime | None:
    """解析 JSON 记忆中的时间字段。

    :param value: 原始时间字段。
    :return: 成功解析时返回 datetime，否则返回 None。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
