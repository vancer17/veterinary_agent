"""
文件：src/vet_agent/services/postgres_memory.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from vet_agent import TrustedIdentity
from vet_agent.db import (
    ConsultationStateModel,
    ConversationTurnModel,
    make_session_factory,
    PetMemoryEpisodeModel,
    PetMemoryFactModel,
)

from .semantic_memory import DisabledSemanticMemory


DEFAULT_TASK_KEY = "__default__"


class PostgresMemoryService:
    def __init__(self, database_url: str, semantic_memory: Any | None = None) -> None:
        """初始化当前对象。

        :param database_url: 数据库连接地址。
        :param semantic_memory: 参数 semantic_memory。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)
        self.semantic_memory = semantic_memory or DisabledSemanticMemory()

    async def read(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取指定范围内的持久化数据。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            turns = session.scalars(
                select(ConversationTurnModel)
                .where(
                    ConversationTurnModel.user_id == identity.user_id,
                    ConversationTurnModel.pet_id == identity.pet_id,
                    ConversationTurnModel.session_id == identity.session_id,
                )
                .order_by(desc(ConversationTurnModel.created_at))
                .limit(20)
            ).all()
            facts = session.scalars(
                select(PetMemoryFactModel)
                .where(
                    PetMemoryFactModel.user_id == identity.user_id,
                    PetMemoryFactModel.pet_id == identity.pet_id,
                    PetMemoryFactModel.is_active.is_(True),
                )
                .order_by(PetMemoryFactModel.fact_type, PetMemoryFactModel.fact_key)
            ).all()
            episodes = session.scalars(
                select(PetMemoryEpisodeModel)
                .where(
                    PetMemoryEpisodeModel.user_id == identity.user_id,
                    PetMemoryEpisodeModel.pet_id == identity.pet_id,
                )
                .order_by(desc(PetMemoryEpisodeModel.created_at))
                .limit(10)
            ).all()
            state_rows = session.scalars(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                )
            ).all()

        semantic, semantic_error = await self._semantic_search(identity, self._semantic_query(turns), limit=5)
        last_summary = turns[0].summary if turns else ""
        return {
            "owner": {},
            "pet": {
                "last_summary": last_summary,
                "turns": [self._turn_dict(row) for row in turns],
                "facts": [self._fact_dict(row) for row in facts],
                "episodes": [self._episode_dict(row) for row in episodes],
                "semantic_memories": semantic,
                "semantic_memory_error": semantic_error,
            },
            "session": {
                "last_summary": last_summary,
                "turns": [self._turn_dict(row) for row in turns],
                "consultation_state": next(
                    (row.state for row in state_rows if row.task_key == DEFAULT_TASK_KEY),
                    {},
                ),
                "task_consultation_states": {
                    row.task_key: row.state
                    for row in state_rows
                    if row.task_key != DEFAULT_TASK_KEY
                },
            },
        }

    async def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """执行 remember_turn 业务逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param medical: 是否属于医疗咨询回合。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        metadata = metadata or {}
        turn_id = str(metadata.get("turn_id") or f"turn_memory_{uuid4().hex}")
        request_id = str(metadata.get("request_id") or f"memory_req_{uuid4().hex}")
        trace_id = str(metadata.get("trace_id") or request_id)
        status = str(metadata.get("status") or "completed")
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
                metadata_json=metadata,
                response_snapshot=metadata.get("response_snapshot"),
            )
            statement = statement.on_conflict_do_nothing(index_elements=["request_id"])
            session.execute(statement)
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
                        metadata_json={"source": "conversation_turn", **metadata},
                    )
                )
            if metadata.get("source") == "memory_correction":
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
        await self._semantic_add_turn(identity, user_text=user_text, summary=summary, metadata=metadata)

    async def read_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """执行 read_consultation_state 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key == DEFAULT_TASK_KEY,
                )
            )
        return dict(row.state) if row else {}

    async def save_consultation_state(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
        """执行 save_consultation_state 业务逻辑。

        :param identity: 可信身份信息。
        :param state: 参数 state。
        :return: 返回函数执行结果。
        """
        self._upsert_state(identity, DEFAULT_TASK_KEY, state)

    async def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """执行 read_task_consultation_states 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            rows = session.scalars(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key != DEFAULT_TASK_KEY,
                )
            ).all()
        return {row.task_key: dict(row.state) for row in rows}

    async def save_task_consultation_states(self, identity: TrustedIdentity, states: dict[str, Any]) -> None:
        """替换当前会话仍未完成的多任务问诊状态。

        :param identity: 可信身份信息。
        :param states: 未完成任务的活跃问诊状态集合。
        :return: 返回函数执行结果。
        """
        with self.session_factory.begin() as session:
            session.execute(
                delete(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key != DEFAULT_TASK_KEY,
                )
            )
            for task_key, state in states.items():
                self._upsert_state_in_session(session, identity, task_key, state)

    async def clear_default_consultation_state(self, identity: TrustedIdentity) -> None:
        """清理当前会话的默认活跃问诊状态。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        with self.session_factory.begin() as session:
            session.execute(
                delete(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key == DEFAULT_TASK_KEY,
                )
            )

    async def clear_consultation_state(self, identity: TrustedIdentity) -> None:
        """清理当前会话所有活跃问诊状态。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        with self.session_factory.begin() as session:
            session.execute(
                delete(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                )
            )

    async def delete_pet_memory(self, pet_id: str, user_id: str | None = None) -> None:
        """执行 delete_pet_memory 业务逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        with self.session_factory.begin() as session:
            turn_where = [ConversationTurnModel.pet_id == pet_id]
            state_where = [ConsultationStateModel.pet_id == pet_id]
            episode_where = [PetMemoryEpisodeModel.pet_id == pet_id]
            fact_where = [PetMemoryFactModel.pet_id == pet_id]
            if user_id:
                turn_where.append(ConversationTurnModel.user_id == user_id)
                state_where.append(ConsultationStateModel.user_id == user_id)
                episode_where.append(PetMemoryEpisodeModel.user_id == user_id)
                fact_where.append(PetMemoryFactModel.user_id == user_id)
            session.execute(delete(ConversationTurnModel).where(*turn_where))
            session.execute(delete(ConsultationStateModel).where(*state_where))
            session.execute(delete(PetMemoryEpisodeModel).where(*episode_where))
            session.execute(delete(PetMemoryFactModel).where(*fact_where))
        await self._semantic_delete_pet(pet_id, user_id=user_id)

    async def upsert_pet_fact(
        self,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float = 1.0,
        source_turn_id: str | None = None,
        source_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """执行 upsert_pet_fact 业务逻辑。

        :param identity: 可信身份信息。
        :param fact_type: 事实类型。
        :param fact_key: 事实键名。
        :param fact_value: 事实内容。
        :param confidence: 置信度。
        :param source_turn_id: 参数 source_turn_id。
        :param source_text: 事实来源文本。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
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
                metadata=metadata or {"source": "manual_correction"},
            )

    def _upsert_state(self, identity: TrustedIdentity, task_key: str, state: dict[str, Any]) -> None:
        """执行 _upsert_state 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param task_key: 参数 task_key。
        :param state: 参数 state。
        :return: 返回函数执行结果。
        """
        with self.session_factory.begin() as session:
            self._upsert_state_in_session(session, identity, task_key, state)

    def _upsert_state_in_session(
        self,
        session: Session,
        identity: TrustedIdentity,
        task_key: str,
        state: dict[str, Any],
    ) -> None:
        """在已有数据库会话中写入问诊状态。

        :param session: 数据库会话。
        :param identity: 可信身份信息。
        :param task_key: 任务状态键。
        :param state: 问诊状态。
        :return: 返回函数执行结果。
        """
        statement = pg_insert(ConsultationStateModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            task_key=task_key,
            state=state,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_consultation_states_scope",
            set_={
                "state": state,
                "version": ConsultationStateModel.version + 1,
                "updated_at": datetime.now(UTC),
            },
        )
        session.execute(statement)

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
        """执行 _upsert_fact_in_session 内部辅助逻辑。

        :param session: 数据库会话。
        :param identity: 可信身份信息。
        :param fact_type: 事实类型。
        :param fact_key: 事实键名。
        :param fact_value: 事实内容。
        :param confidence: 置信度。
        :param source_turn_id: 参数 source_turn_id。
        :param source_text: 事实来源文本。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
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
        statement = statement.on_conflict_do_update(
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
        session.execute(statement)

    async def _semantic_search(
        self,
        identity: TrustedIdentity,
        query: str,
        *,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """执行 _semantic_search 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        try:
            return await self.semantic_memory.search(identity, query, limit=limit), None
        except Exception as exc:
            return [], type(exc).__name__

    async def _semantic_add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        """执行 _semantic_add_turn 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        try:
            await self.semantic_memory.add_turn(identity, user_text=user_text, summary=summary, metadata=metadata)
        except Exception:
            return None

    async def _semantic_delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """执行 _semantic_delete_pet 内部辅助逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        try:
            await self.semantic_memory.delete_pet(pet_id, user_id=user_id)
        except Exception:
            return None

    def _semantic_query(self, turns: list[ConversationTurnModel]) -> str:
        """执行 _semantic_query 内部辅助逻辑。

        :param turns: 参数 turns。
        :return: 返回函数执行结果。
        """
        return turns[0].input_text if turns else "pet memory"

    def _episode_title(self, user_text: str) -> str:
        """执行 _episode_title 内部辅助逻辑。

        :param user_text: 用户输入文本。
        :return: 返回函数执行结果。
        """
        return (user_text.strip().splitlines()[0] or "本轮咨询")[:80]

    def _turn_dict(self, row: ConversationTurnModel) -> dict[str, Any]:
        """执行 _turn_dict 内部辅助逻辑。

        :param row: 数据库行。
        :return: 返回函数执行结果。
        """
        return {
            "turn_id": row.turn_id,
            "request_id": row.request_id,
            "trace_id": row.trace_id,
            "user_text": row.input_text,
            "summary": row.summary,
            "medical": row.medical,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _fact_dict(self, row: PetMemoryFactModel) -> dict[str, Any]:
        """执行 _fact_dict 内部辅助逻辑。

        :param row: 数据库行。
        :return: 返回函数执行结果。
        """
        return {
            "fact_type": row.fact_type,
            "fact_key": row.fact_key,
            "fact_value": row.fact_value,
            "confidence": row.confidence,
            "source_turn_id": row.source_turn_id,
            "source_text": row.source_text,
            "metadata": row.metadata_json or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _episode_dict(self, row: PetMemoryEpisodeModel) -> dict[str, Any]:
        """执行 _episode_dict 内部辅助逻辑。

        :param row: 数据库行。
        :return: 返回函数执行结果。
        """
        return {
            "title": row.title,
            "summary": row.summary,
            "memory_scope": row.memory_scope,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
