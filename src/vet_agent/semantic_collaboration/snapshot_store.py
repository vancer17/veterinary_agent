"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/snapshot_store.py
作用：实现受限语义协作 DAG M02 的 TurnSnapshot 持久化边界。
范围：覆盖不可变快照保存、digest 身份冲突检测、按 digest 读取、
      strict Pydantic 反序列化以及 M06 worker 可复用的读取 Adapter。
说明：本文件不构建 TurnSnapshot、不调用模型、不读取问诊状态或长期记忆，
      也不把来源失败转换为空快照；快照保存后不允许原地更新。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from vet_agent.db import (
    SemanticTurnSnapshotModel,
    make_session_factory,
)

from .errors import (
    SemanticTurnSnapshotConflictError,
    SemanticTurnSnapshotStoreError,
)
from .snapshot_contracts import (
    TurnSnapshot,
    TurnSnapshotSourceScope,
    canonical_turn_snapshot_json,
)


def _snapshot_json(snapshot: TurnSnapshot) -> str:
    """生成 TurnSnapshot 的 canonical 存档 JSON。

    :param snapshot: 已通过自身 digest 校验的不可变快照。
    :return: 返回排序键、紧凑且保留 Unicode 的完整快照 JSON。
    """
    return canonical_turn_snapshot_json(snapshot.model_dump(mode="json"))


def _snapshot_scope_matches(
    row: SemanticTurnSnapshotModel,
    scope: TurnSnapshotSourceScope,
) -> bool:
    """校验存档行与本次快照的可信范围一致。

    :param row: 数据库中的快照行。
    :param scope: 服务侧传入的可信范围。
    :return: 用户、会话和宠物范围完全一致时返回 True。
    """
    return (
        row.user_id,
        row.session_id,
        row.pet_id,
    ) == (
        scope.user_id,
        scope.session_id,
        scope.pet_id,
    )


class SemanticTurnSnapshotStore(Protocol):
    """表示 TurnSnapshot append-only 存档端口。

    :return: 无返回值；业务层不得直接访问具体数据库表模型。
    """

    def save(
        self,
        snapshot: TurnSnapshot,
        *,
        scope: TurnSnapshotSourceScope,
    ) -> None:
        """幂等保存一个不可变 TurnSnapshot。

        :param snapshot: 待保存的完整快照。
        :param scope: 构建该快照的可信范围；scope 不参与 snapshot digest。
        :return: 无返回值；同身份同内容重放成功，身份冲突显式失败。
        """

    def load(self, context_digest: str) -> TurnSnapshot:
        """按 digest 读取不可变 TurnSnapshot。

        :param context_digest: 快照权威 SHA-256 digest。
        :return: 返回重新通过 digest 校验的完整快照。
        """


@dataclass
class InMemorySemanticTurnSnapshotStore(SemanticTurnSnapshotStore):
    """提供进程内 TurnSnapshot 存档测试与本地开发实现。

    :return: 无返回值；该实现不提供跨进程持久化或并发锁语义。
    """

    rows: dict[str, tuple[TurnSnapshot, TurnSnapshotSourceScope]] = field(
        default_factory=dict,
    )

    def save(
        self,
        snapshot: TurnSnapshot,
        *,
        scope: TurnSnapshotSourceScope,
    ) -> None:
        """保存或幂等重放一个进程内快照。

        :param snapshot: 待保存的完整快照。
        :param scope: 快照可信范围。
        :return: 无返回值。
        :raises SemanticTurnSnapshotConflictError: digest、turn_id 或范围冲突时抛出。
        """
        existing = self.rows.get(snapshot.context_digest)
        if existing is not None:
            if existing[0] != snapshot or existing[1] != scope:
                raise SemanticTurnSnapshotConflictError(
                    "turn snapshot context digest conflict",
                )
            return
        for existing_snapshot, existing_scope in self.rows.values():
            if (
                existing_snapshot.turn_id == snapshot.turn_id
                and existing_snapshot.context_digest != snapshot.context_digest
            ):
                raise SemanticTurnSnapshotConflictError(
                    "turn snapshot turn id conflict",
                )
            if (
                existing_scope == scope
                and existing_snapshot.turn_index == snapshot.turn_index
                and existing_snapshot.context_digest != snapshot.context_digest
            ):
                raise SemanticTurnSnapshotConflictError(
                    "turn snapshot scope turn index conflict",
                )
        self.rows[snapshot.context_digest] = (snapshot, scope)

    def load(self, context_digest: str) -> TurnSnapshot:
        """读取进程内快照并复验 digest。

        :param context_digest: 快照 digest。
        :return: 返回完整快照。
        :raises SemanticTurnSnapshotStoreError: 快照缺失时抛出。
        """
        row = self.rows.get(context_digest)
        if row is None:
            raise SemanticTurnSnapshotStoreError("turn snapshot is not found")
        snapshot = row[0]
        snapshot.verify_digest(context_digest)
        return snapshot


class PostgresSemanticTurnSnapshotStore(SemanticTurnSnapshotStore):
    """提供 PostgreSQL append-only TurnSnapshot 存档。

    :return: 无返回值；本仓储是唯一访问快照表模型的生产边界。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL TurnSnapshot 仓储。

        :param database_url: PostgreSQL 连接地址。
        :return: 无返回值。
        """
        self.session_factory: sessionmaker[Session] = make_session_factory(
            database_url,
        )

    def save(
        self,
        snapshot: TurnSnapshot,
        *,
        scope: TurnSnapshotSourceScope,
    ) -> None:
        """幂等保存 TurnSnapshot。

        :param snapshot: 待保存的完整快照。
        :param scope: 快照可信范围。
        :return: 无返回值。
        :raises SemanticTurnSnapshotConflictError: digest、turn_id 或范围冲突时抛出。
        :raises SemanticTurnSnapshotStoreError: 数据库访问失败时抛出。
        """
        snapshot_json = _snapshot_json(snapshot)
        statement = pg_insert(SemanticTurnSnapshotModel).values(
            context_digest=snapshot.context_digest,
            turn_id=snapshot.turn_id,
            turn_index=snapshot.turn_index,
            user_id=scope.user_id,
            session_id=scope.session_id,
            pet_id=scope.pet_id,
            snapshot_version=snapshot.snapshot_version,
            snapshot_json=snapshot_json,
        )
        try:
            with self.session_factory.begin() as session:
                session.execute(
                    statement.on_conflict_do_nothing(
                        index_elements=["context_digest"],
                    ),
                )
                row = session.scalar(
                    select(SemanticTurnSnapshotModel).where(
                        SemanticTurnSnapshotModel.context_digest
                        == snapshot.context_digest,
                    ),
                )
                if (
                    row is None
                    or row.turn_id != snapshot.turn_id
                    or row.snapshot_json != snapshot_json
                    or not _snapshot_scope_matches(row, scope)
                ):
                    raise SemanticTurnSnapshotConflictError(
                        "turn snapshot identity conflict",
                    )
        except SQLAlchemyError as error:
            raise SemanticTurnSnapshotStoreError(
                "failed to persist turn snapshot",
            ) from error

    def load(self, context_digest: str) -> TurnSnapshot:
        """按 digest 读取并校验 TurnSnapshot。

        :param context_digest: 快照权威 digest。
        :return: 返回完整快照。
        :raises SemanticTurnSnapshotStoreError: 缺失、损坏或数据库访问失败时抛出。
        """
        try:
            with self.session_factory() as session:
                row = session.scalar(
                    select(SemanticTurnSnapshotModel).where(
                        SemanticTurnSnapshotModel.context_digest == context_digest,
                    ),
                )
        except SQLAlchemyError as error:
            raise SemanticTurnSnapshotStoreError(
                "failed to read turn snapshot",
            ) from error
        if row is None:
            raise SemanticTurnSnapshotStoreError("turn snapshot is not found")
        try:
            snapshot = TurnSnapshot.model_validate_json(row.snapshot_json)
        except ValidationError as error:
            raise SemanticTurnSnapshotStoreError(
                "turn snapshot archive is invalid",
            ) from error
        if snapshot.context_digest != context_digest:
            raise SemanticTurnSnapshotStoreError(
                "turn snapshot archive digest mismatch",
            )
        return snapshot


@dataclass(frozen=True)
class PersistentTurnSnapshotReader:
    """把 TurnSnapshot 存档端口适配为 M06 worker 读取端口。

    :return: 无返回值；该 Adapter 不缓存、不重建、不返回摘要替代品。
    """

    store: SemanticTurnSnapshotStore

    async def load(self, turn_snapshot_digest: str) -> TurnSnapshot:
        """读取指定 digest 的权威 TurnSnapshot。

        :param turn_snapshot_digest: 任务绑定的上下文 digest。
        :return: 返回完整不可变快照。
        """
        return self.store.load(turn_snapshot_digest)


__all__ = [
    "InMemorySemanticTurnSnapshotStore",
    "PersistentTurnSnapshotReader",
    "PostgresSemanticTurnSnapshotStore",
    "SemanticTurnSnapshotStore",
]
