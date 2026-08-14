"""
=============================================================================
文件：src/vet_agent/repositories/memory_write.py
作用：提供 PostgreSQL 结构化记忆写入与宠物范围删除仓储。
范围：封装 conversation_turns、pet_memory_episodes、pet_memory_facts 的写入，
      以及指定用户和宠物范围内的结构化记忆删除。
说明：业务服务只负责编排回合记忆写入、问诊状态写入和 Mem0 投影写入；
      不得直接访问本文件封装的 SQLAlchemy 数据表模型。
=============================================================================
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vet_agent import TrustedIdentity
from vet_agent.db import (
    ConversationTurnModel,
    PetMemoryEpisodeModel,
    PetMemoryFactModel,
    make_session_factory,
)


class MemoryWriteRepository(Protocol):
    """定义结构化记忆写入仓储协议。

    说明：协议隔离业务服务、SQLAlchemy 数据表和数据库会话；
    实现类必须显式继承本协议，以便追踪调用链并替换测试实现。

    :return: 无返回值。
    """

    def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入当前回合的结构化记忆与可选中期 episode。

        :param identity: 当前可信用户、宠物与会话范围。
        :param user_text: 用户输入文本。
        :param summary: Agent 回合摘要或响应摘要。
        :param medical: 当前回合是否属于医疗咨询主链路。
        :param metadata: 回合附加审计元数据。
        :return: 无返回值。
        """
        ...

    def upsert_pet_fact(
        self,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_turn_id: str | None,
        source_text: str | None,
        metadata: dict[str, Any],
    ) -> None:
        """写入或更新一条受控宠物长期事实。

        :param identity: 当前可信用户与宠物范围。
        :param fact_type: 长期事实类型。
        :param fact_key: 长期事实稳定键名。
        :param fact_value: 长期事实内容。
        :param confidence: 长期事实置信度。
        :param source_turn_id: 事实来源回合标识。
        :param source_text: 事实来源文本。
        :param metadata: 事实写入审计元数据。
        :return: 无返回值。
        """
        ...

    def delete_for_pet(self, pet_id: str, user_id: str | None = None) -> None:
        """删除指定用户与宠物范围内的结构化记忆。

        :param pet_id: 待删除的宠物标识。
        :param user_id: 可选的用户范围限制。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查结构化记忆写入仓储是否可访问。

        :return: 依赖数据表可访问时返回 True。
        """
        ...


class PostgresMemoryWriteRepository(MemoryWriteRepository):
    """基于 SQLAlchemy 的 PostgreSQL 结构化记忆写入仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 结构化记忆写入仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入 PostgreSQL 当前回合记忆与可选中期 episode。

        :param identity: 当前可信用户、宠物与会话范围。
        :param user_text: 用户输入文本。
        :param summary: Agent 回合摘要或响应摘要。
        :param medical: 当前回合是否属于医疗咨询主链路。
        :param metadata: 回合附加审计元数据。
        :return: 无返回值。
        """
        normalized_metadata = dict(metadata or {})
        turn_id = str(normalized_metadata.get("turn_id") or f"turn_memory_{uuid4().hex}")
        request_id = str(normalized_metadata.get("request_id") or f"memory_req_{uuid4().hex}")
        trace_id = str(normalized_metadata.get("trace_id") or request_id)
        status = str(normalized_metadata.get("status") or "completed")
        with self.session_factory.begin() as session:
            statement = pg_insert(ConversationTurnModel).values(
                turn_id=turn_id,
                request_id=request_id,
                trace_id=trace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                pet_id=identity.pet_id,
                input_text=user_text[:4000],
                summary=summary[:4000],
                status=status,
                medical=medical,
                metadata_json=normalized_metadata,
                response_snapshot=normalized_metadata.get("response_snapshot"),
            )
            session.execute(statement.on_conflict_do_nothing(index_elements=["request_id"]))
            if medical:
                session.add(
                    PetMemoryEpisodeModel(
                        user_id=identity.user_id,
                        pet_id=identity.pet_id,
                        session_id=identity.session_id,
                        turn_id=turn_id,
                        title=self._episode_title(user_text),
                        summary=summary[:1200],
                        memory_scope="medium",
                        metadata_json={"source": "conversation_turn", **normalized_metadata},
                    )
                )
            if normalized_metadata.get("source") == "memory_correction":
                self._upsert_fact_in_session(
                    session,
                    identity,
                    fact_type="owner_preference",
                    fact_key="answer_style",
                    fact_value=summary[:1000],
                    confidence=1.0,
                    source_turn_id=turn_id,
                    source_text=user_text,
                    metadata={"source": "user_correction"},
                )

    def upsert_pet_fact(
        self,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_turn_id: str | None,
        source_text: str | None,
        metadata: dict[str, Any],
    ) -> None:
        """写入或更新 PostgreSQL 宠物长期事实。

        :param identity: 当前可信用户与宠物范围。
        :param fact_type: 长期事实类型。
        :param fact_key: 长期事实稳定键名。
        :param fact_value: 长期事实内容。
        :param confidence: 长期事实置信度。
        :param source_turn_id: 事实来源回合标识。
        :param source_text: 事实来源文本。
        :param metadata: 事实写入审计元数据。
        :return: 无返回值。
        """
        with self.session_factory.begin() as session:
            self._upsert_fact_in_session(
                session,
                identity,
                fact_type=fact_type,
                fact_key=fact_key,
                fact_value=fact_value,
                confidence=confidence,
                source_turn_id=source_turn_id,
                source_text=source_text,
                metadata=metadata,
            )

    def delete_for_pet(self, pet_id: str, user_id: str | None = None) -> None:
        """删除 PostgreSQL 指定用户与宠物范围内的结构化记忆。

        :param pet_id: 待删除的宠物标识。
        :param user_id: 可选的用户范围限制。
        :return: 无返回值。
        """
        turn_filters = [ConversationTurnModel.pet_id == pet_id]
        episode_filters = [PetMemoryEpisodeModel.pet_id == pet_id]
        fact_filters = [PetMemoryFactModel.pet_id == pet_id]
        if user_id is not None:
            turn_filters.append(ConversationTurnModel.user_id == user_id)
            episode_filters.append(PetMemoryEpisodeModel.user_id == user_id)
            fact_filters.append(PetMemoryFactModel.user_id == user_id)
        with self.session_factory.begin() as session:
            session.execute(delete(ConversationTurnModel).where(*turn_filters))
            session.execute(delete(PetMemoryEpisodeModel).where(*episode_filters))
            session.execute(delete(PetMemoryFactModel).where(*fact_filters))

    def is_ready(self) -> bool:
        """检查 PostgreSQL 结构化记忆写入表是否可访问。

        :return: 依赖数据表可访问时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(select(ConversationTurnModel.id).limit(1))
            return True
        except SQLAlchemyError:
            return False

    def _upsert_fact_in_session(
        self,
        session: Session,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_turn_id: str | None,
        source_text: str | None,
        metadata: dict[str, Any],
    ) -> None:
        """在已有 SQLAlchemy 事务中写入一条宠物长期事实。

        :param session: 当前 SQLAlchemy 数据库会话。
        :param identity: 当前可信用户与宠物范围。
        :param fact_type: 长期事实类型。
        :param fact_key: 长期事实稳定键名。
        :param fact_value: 长期事实内容。
        :param confidence: 长期事实置信度。
        :param source_turn_id: 事实来源回合标识。
        :param source_text: 事实来源文本。
        :param metadata: 事实写入审计元数据。
        :return: 无返回值。
        """
        statement = pg_insert(PetMemoryFactModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            fact_type=fact_type,
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_turn_id=source_turn_id,
            source_text=source_text,
            is_active=True,
            metadata_json=metadata,
            updated_at=datetime.now(UTC),
        )
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_pet_memory_facts_key",
                set_={
                    "fact_value": fact_value,
                    "confidence": confidence,
                    "source_turn_id": source_turn_id,
                    "source_text": source_text,
                    "is_active": True,
                    "metadata": metadata,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    def _episode_title(self, user_text: str) -> str:
        """生成中期 episode 的展示标题。

        :param user_text: 当前回合用户输入文本。
        :return: 返回截断后的 episode 标题。
        """
        return (user_text.strip().splitlines()[0] or "本轮咨询")[:80]
