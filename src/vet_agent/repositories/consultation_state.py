"""
=============================================================================
文件：src/vet_agent/repositories/consultation_state.py
作用：提供活跃问诊状态的数据库仓储协议及 PostgreSQL、JSON 测试实现。
范围：封装 consultation_states 的默认任务状态、多任务状态、清理和宠物范围删除。
说明：业务服务不得直接访问 ConsultationStateModel；所有状态持久化操作必须通过本文件
      暴露的 ConsultationStateRepository 协议完成。JSON 实现仅用于显式测试或嵌入场景。
=============================================================================
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from vet_agent import TrustedIdentity
from vet_agent.db import ConsultationStateModel, make_session_factory
from vet_agent.stores import JsonDocumentStore


DEFAULT_TASK_KEY = "__default__"


class ConsultationStateRepository(Protocol):
    """定义活跃问诊状态的持久化仓储协议。

    说明：协议隔离状态模型、数据库会话与问诊业务编排；
    实现类必须显式继承本协议，便于追踪依赖和替换测试实现。

    :return: 无返回值。
    """

    def read_default(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前范围内的默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回默认任务状态；不存在时返回空字典。
        """
        ...

    def read_tasks(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取当前范围内的多任务问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回任务键到状态字典的映射。
        """
        ...

    def save_default(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
        """保存当前范围内的默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :param state: 待保存的结构化问诊状态。
        :return: 无返回值。
        """
        ...

    def replace_tasks(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
        *,
        clear_default: bool = False,
    ) -> None:
        """替换当前范围内的多任务问诊状态集合。

        :param identity: 可信用户、宠物与会话范围。
        :param states: 未完成任务的状态集合。
        :param clear_default: 是否同时清理默认任务状态。
        :return: 无返回值。
        """
        ...

    def clear_default(self, identity: TrustedIdentity) -> None:
        """清理当前范围内的默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        ...

    def clear_all(self, identity: TrustedIdentity) -> None:
        """清理当前范围内的全部活跃问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        ...

    def delete_for_pet(self, pet_id: str, user_id: str | None = None) -> None:
        """删除指定宠物范围内的活跃问诊状态。

        :param pet_id: 宠物标识。
        :param user_id: 可选的用户范围限制。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查活跃问诊状态仓储是否可用。

        :return: 依赖可访问时返回 True。
        """
        ...


class PostgresConsultationStateRepository(ConsultationStateRepository):
    """基于 SQLAlchemy 的 PostgreSQL 活跃问诊状态仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 活跃问诊状态仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def read_default(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 PostgreSQL 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回默认任务状态；不存在时返回空字典。
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
        return dict(row.state) if row is not None else {}

    def read_tasks(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 PostgreSQL 多任务问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回任务键到状态字典的映射。
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

    def save_default(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
        """保存 PostgreSQL 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :param state: 待保存的结构化问诊状态。
        :return: 无返回值。
        """
        with self.session_factory.begin() as session:
            self._upsert_state(session, identity, DEFAULT_TASK_KEY, state)

    def replace_tasks(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
        *,
        clear_default: bool = False,
    ) -> None:
        """替换 PostgreSQL 多任务问诊状态集合。

        :param identity: 可信用户、宠物与会话范围。
        :param states: 未完成任务的状态集合。
        :param clear_default: 是否同时清理默认任务状态。
        :return: 无返回值。
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
                self._upsert_state(session, identity, task_key, state)
            if clear_default:
                session.execute(
                    delete(ConsultationStateModel).where(
                        ConsultationStateModel.user_id == identity.user_id,
                        ConsultationStateModel.pet_id == identity.pet_id,
                        ConsultationStateModel.session_id == identity.session_id,
                        ConsultationStateModel.task_key == DEFAULT_TASK_KEY,
                    )
                )

    def clear_default(self, identity: TrustedIdentity) -> None:
        """清理 PostgreSQL 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
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

    def clear_all(self, identity: TrustedIdentity) -> None:
        """清理 PostgreSQL 全部活跃问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        with self.session_factory.begin() as session:
            session.execute(
                delete(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                )
            )

    def delete_for_pet(self, pet_id: str, user_id: str | None = None) -> None:
        """删除 PostgreSQL 指定宠物范围内的活跃问诊状态。

        :param pet_id: 宠物标识。
        :param user_id: 可选的用户范围限制。
        :return: 无返回值。
        """
        filters = [ConsultationStateModel.pet_id == pet_id]
        if user_id is not None:
            filters.append(ConsultationStateModel.user_id == user_id)
        with self.session_factory.begin() as session:
            session.execute(delete(ConsultationStateModel).where(*filters))

    def is_ready(self) -> bool:
        """检查 PostgreSQL 活跃问诊状态表是否可访问。

        :return: 数据表可访问时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(select(ConsultationStateModel.id).limit(1))
            return True
        except Exception:
            return False

    def _upsert_state(
        self,
        session: Session,
        identity: TrustedIdentity,
        task_key: str,
        state: dict[str, Any],
    ) -> None:
        """在 PostgreSQL 事务中写入一个任务问诊状态。

        :param session: SQLAlchemy 数据库会话。
        :param identity: 可信用户、宠物与会话范围。
        :param task_key: 任务状态键。
        :param state: 待保存的结构化问诊状态。
        :return: 无返回值。
        """
        statement = pg_insert(ConsultationStateModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            task_key=task_key,
            state=state,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_consultation_states_scope",
            set_={
                "state": state,
                "version": ConsultationStateModel.version + 1,
            },
        )
        session.execute(statement)


class JsonConsultationStateRepository(ConsultationStateRepository):
    """基于 JSON 文档的活跃问诊状态仓储，仅用于显式测试或嵌入场景。

    :return: 无返回值。
    """

    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化 JSON 活跃问诊状态仓储。

        :param store: JSON 文档存储。
        :return: 无返回值。
        """
        self.store = store

    def read_default(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回默认任务状态；不存在时返回空字典。
        """
        data = self.store.load()
        return dict(data.get("sessions", {}).get(identity.session_id, {}).get("consultation_state", {}))

    def read_tasks(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取 JSON 多任务问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 返回任务键到状态字典的映射。
        """
        data = self.store.load()
        states = data.get("sessions", {}).get(identity.session_id, {}).get("task_consultation_states", {})
        return dict(states) if isinstance(states, dict) else {}

    def save_default(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
        """保存 JSON 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :param state: 待保存的结构化问诊状态。
        :return: 无返回值。
        """
        data = self.store.load()
        self._ensure_scope(data, identity)
        data["sessions"][identity.session_id]["consultation_state"] = state
        data["pets"][identity.pet_id]["consultation_state"] = state
        self.store.save(data)

    def replace_tasks(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
        *,
        clear_default: bool = False,
    ) -> None:
        """替换 JSON 多任务问诊状态集合。

        :param identity: 可信用户、宠物与会话范围。
        :param states: 未完成任务的状态集合。
        :param clear_default: 是否同时清理默认任务状态。
        :return: 无返回值。
        """
        data = self.store.load()
        self._ensure_scope(data, identity)
        data["sessions"][identity.session_id]["task_consultation_states"] = states
        data["pets"][identity.pet_id]["task_consultation_states"] = states
        if clear_default:
            data["sessions"][identity.session_id].pop("consultation_state", None)
            data["pets"][identity.pet_id].pop("consultation_state", None)
        self.store.save(data)

    def clear_default(self, identity: TrustedIdentity) -> None:
        """清理 JSON 默认问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        data = self.store.load()
        data.get("sessions", {}).get(identity.session_id, {}).pop("consultation_state", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("consultation_state", None)
        self.store.save(data)

    def clear_all(self, identity: TrustedIdentity) -> None:
        """清理 JSON 全部活跃问诊状态。

        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        data = self.store.load()
        data.get("sessions", {}).get(identity.session_id, {}).pop("consultation_state", None)
        data.get("sessions", {}).get(identity.session_id, {}).pop("task_consultation_states", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("consultation_state", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("task_consultation_states", None)
        self.store.save(data)

    def delete_for_pet(self, pet_id: str, user_id: str | None = None) -> None:
        """删除 JSON 指定宠物范围内的活跃问诊状态。

        :param pet_id: 宠物标识。
        :param user_id: 可选的用户范围限制；JSON 测试存储不保留完整状态索引。
        :return: 无返回值。
        """
        data = self.store.load()
        pet_memory = data.get("pets", {}).get(pet_id)
        if isinstance(pet_memory, dict) and self._scope_matches(pet_memory, user_id=user_id):
            pet_memory.pop("consultation_state", None)
            pet_memory.pop("task_consultation_states", None)
        for session_memory in data.get("sessions", {}).values():
            if not isinstance(session_memory, dict):
                continue
            if session_memory.get("pet_id") == pet_id and self._scope_matches(session_memory, user_id=user_id):
                session_memory.pop("consultation_state", None)
                session_memory.pop("task_consultation_states", None)
        self.store.save(data)

    def is_ready(self) -> bool:
        """检查 JSON 活跃问诊状态仓储是否可用。

        :return: JSON 文档存储可读写时返回 True。
        """
        return True

    def _ensure_scope(self, data: dict[str, Any], identity: TrustedIdentity) -> None:
        """确保 JSON 文档包含当前身份范围的基础节点。

        :param data: JSON 文档数据。
        :param identity: 可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        pet_memory = data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        pet_memory.setdefault("user_id", identity.user_id)
        session_memory.setdefault("user_id", identity.user_id)
        session_memory.setdefault("pet_id", identity.pet_id)

    def _scope_matches(self, memory: dict[str, Any], *, user_id: str | None) -> bool:
        """判断 JSON 测试存储节点是否属于指定用户范围。

        :param memory: JSON 记忆节点。
        :param user_id: 可选的用户范围限制。
        :return: 未提供用户范围或节点用户匹配时返回 True。
        """
        return user_id is None or memory.get("user_id") in {None, user_id}
