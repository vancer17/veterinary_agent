"""
文件：src/vet_agent/repositories/scope.py
作用：提供身份、宠物资料与会话范围数据链的仓储契约和 PostgreSQL 实现。
范围：仅允许本文件直接访问宠物画像与会话绑定数据表模型；业务服务需通过 ScopeRepository 协议访问数据。
说明：pet_profiles 保存上游已验证宠物画像在 Agent 侧的本地投影，不作为主服务宠物归属的独立权威源。
说明：本文件位于 src-layout 下的 repositories 包内，跨包调用应通过 vet_agent.repositories 顶层导出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vet_agent import TrustedIdentity
from vet_agent.db import PetProfileModel, PetSessionBindingModel, make_session_factory


@dataclass(frozen=True)
class VerifiedPetProfile:
    """表示服务端已验证宠物画像在范围数据链中的只读投影。

    :param user_id: 宠物所属用户标识。
    :param pet_id: 宠物标识。
    :param profile: 已验证宠物画像 JSON。
    :param source: 已验证画像来源。
    :param is_active: 宠物资料是否处于启用状态。
    :param created_at: 资料创建时间。
    :param updated_at: 资料更新时间。
    :return: 无返回值。
    """

    user_id: str
    pet_id: str
    profile: dict[str, Any] = field(default_factory=dict)
    source: str = "api"
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SessionBinding:
    """表示会话与用户、宠物范围的绑定关系。

    :param session_id: 会话标识。
    :param user_id: 会话绑定的用户标识。
    :param pet_id: 会话绑定的宠物标识。
    :param created_at: 绑定创建时间。
    :param updated_at: 绑定更新时间。
    :param last_seen_at: 绑定最近一次访问时间。
    :return: 无返回值。
    """

    session_id: str
    user_id: str
    pet_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None


class ScopeRepository(Protocol):
    """定义身份、宠物资料与会话范围仓储协议。

    :return: 无返回值。
    """

    def get_pet_profile(self, identity: TrustedIdentity) -> VerifiedPetProfile | None:
        """读取当前宠物标识对应的已验证宠物画像。

        :param identity: 本轮可信身份范围。
        :return: 存在已验证宠物画像时返回画像投影，否则返回 None。
        """
        ...

    def upsert_pet_profile(
        self,
        identity: TrustedIdentity,
        *,
        profile: dict[str, Any],
        source: str,
        is_active: bool,
    ) -> VerifiedPetProfile:
        """写入或刷新上游已验证宠物画像在 Agent 侧的本地投影。

        :param identity: 本轮可信身份范围。
        :param profile: 上游已验证宠物画像。
        :param source: 上游画像来源。
        :param is_active: 宠物画像是否启用。
        :return: 返回写入后的画像投影。
        """
        ...

    def get_session_binding(self, session_id: str) -> SessionBinding | None:
        """读取会话范围绑定关系。

        :param session_id: 会话标识。
        :return: 存在会话绑定时返回绑定投影，否则返回 None。
        """
        ...

    def bind_session(self, identity: TrustedIdentity) -> SessionBinding | None:
        """在数据库约束保护下创建会话范围绑定。

        :param identity: 本轮可信身份范围。
        :return: 创建后或并发已存在的会话绑定投影。
        """
        ...

    def touch_session(self, identity: TrustedIdentity) -> None:
        """更新当前一致会话绑定的最近访问时间。

        :param identity: 本轮可信身份范围。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查范围仓储依赖的数据表是否可访问。

        :return: 数据库表可访问时返回 True。
        """
        ...


class PostgresScopeRepository(ScopeRepository):
    """基于 PostgreSQL 的范围仓储实现。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 范围仓储。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def get_pet_profile(self, identity: TrustedIdentity) -> VerifiedPetProfile | None:
        """读取当前宠物标识对应的已验证宠物画像。

        :param identity: 本轮可信身份范围。
        :return: 存在已验证宠物画像时返回画像投影，否则返回 None。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(PetProfileModel).where(
                    PetProfileModel.user_id == identity.user_id,
                    PetProfileModel.pet_id == identity.pet_id,
                )
            )
        return _pet_profile_from_row(row) if row is not None else None

    def upsert_pet_profile(
        self,
        identity: TrustedIdentity,
        *,
        profile: dict[str, Any],
        source: str,
        is_active: bool,
    ) -> VerifiedPetProfile:
        """写入或刷新上游已验证宠物画像在 Agent 侧的本地投影。

        :param identity: 本轮可信身份范围。
        :param profile: 上游已验证宠物画像。
        :param source: 上游画像来源。
        :param is_active: 宠物画像是否启用。
        :return: 返回写入后的画像投影。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetProfileModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            profile=profile,
            source=source,
            is_active=is_active,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_profiles_pet_id",
            set_={
                "user_id": identity.user_id,
                "profile": profile,
                "source": source,
                "is_active": is_active,
                "updated_at": now,
            },
        )
        with self.session_factory.begin() as session:
            session.execute(statement)
        refreshed = self.get_pet_profile(identity)
        if refreshed is None:
            raise RuntimeError("failed to upsert pet profile projection")
        return refreshed

    def get_session_binding(self, session_id: str) -> SessionBinding | None:
        """读取会话范围绑定关系。

        :param session_id: 会话标识。
        :return: 存在会话绑定时返回绑定投影，否则返回 None。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(PetSessionBindingModel).where(PetSessionBindingModel.session_id == session_id)
            )
        return _session_binding_from_row(row) if row is not None else None

    def bind_session(self, identity: TrustedIdentity) -> SessionBinding | None:
        """在数据库唯一约束保护下创建会话范围绑定。

        :param identity: 本轮可信身份范围。
        :return: 创建后或并发已存在的会话绑定投影。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetSessionBindingModel).values(
            session_id=identity.session_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            updated_at=now,
            last_seen_at=now,
        )
        statement = statement.on_conflict_do_nothing(constraint="uq_pet_session_bindings_session_id")
        with self.session_factory.begin() as session:
            session.execute(statement)
        return self.get_session_binding(identity.session_id)

    def touch_session(self, identity: TrustedIdentity) -> None:
        """更新当前一致会话绑定的最近访问时间。

        :param identity: 本轮可信身份范围。
        :return: 无返回值。
        """
        now = datetime.now(UTC)
        statement = pg_insert(PetSessionBindingModel).values(
            session_id=identity.session_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            updated_at=now,
            last_seen_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_session_bindings_session_id",
            set_={"updated_at": now, "last_seen_at": now},
            where=(
                (PetSessionBindingModel.user_id == identity.user_id)
                & (PetSessionBindingModel.pet_id == identity.pet_id)
            ),
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    def is_ready(self) -> bool:
        """检查范围仓储依赖的数据表是否可访问。

        :return: 数据库表可访问时返回 True。
        """
        try:
            with self.session_factory() as session:
                _probe_table(session, PetProfileModel)
                _probe_table(session, PetSessionBindingModel)
            return True
        except SQLAlchemyError:
            return False


def _pet_profile_from_row(row: PetProfileModel) -> VerifiedPetProfile:
    """将宠物画像数据表行转换为范围数据链只读投影。

    :param row: 宠物画像数据表行。
    :return: 返回已验证宠物画像投影。
    """
    return VerifiedPetProfile(
        user_id=row.user_id,
        pet_id=row.pet_id,
        profile=dict(row.profile or {}),
        source=row.source,
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_binding_from_row(row: PetSessionBindingModel) -> SessionBinding:
    """将会话绑定数据表行转换为范围数据链只读投影。

    :param row: 会话绑定数据表行。
    :return: 返回会话绑定投影。
    """
    return SessionBinding(
        session_id=row.session_id,
        user_id=row.user_id,
        pet_id=row.pet_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_seen_at=row.last_seen_at,
    )


def _probe_table(session: Session, model: type[PetProfileModel] | type[PetSessionBindingModel]) -> None:
    """对范围数据表执行轻量访问探测。

    :param session: SQLAlchemy 数据库会话。
    :param model: 待探测的数据表模型。
    :return: 无返回值；查询成功表示数据表可访问。
    """
    session.execute(select(model.id).limit(1)).first()
