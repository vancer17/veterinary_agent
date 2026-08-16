"""
=============================================================================
文件：src/vet_agent/repositories/background_tasks.py
作用：提供持久化后台任务的 PostgreSQL 仓储契约与实现。
范围：封装 background_tasks 表的入队、领取、完成、重试与死信标记能力；
      不承载任务业务逻辑，不直接执行长期记忆抽取或 Mem0 投影。
说明：本仓储属于可持久化后台任务基础设施数据链，业务层不得直接访问 SQLAlchemy
      数据表模型，必须通过本文件暴露的仓储协议完成读写。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vet_agent import TrustedIdentity
from vet_agent.background_tasks import BackgroundTaskRecord, BackgroundTaskStatus, BackgroundTaskType
from vet_agent.db import BackgroundTaskModel, make_session_factory


class BackgroundTaskRepositoryError(RuntimeError):
    """表示后台任务仓储访问失败。

    :return: 无返回值。
    """


class BackgroundTaskRepository(Protocol):
    """定义可持久化后台任务仓储协议。

    说明：协议用于隔离业务服务、SQLAlchemy 数据表和数据库会话；
    实现类必须显式继承本协议，以便追踪调用链并替换测试实现。

    :return: 无返回值。
    """

    def enqueue_task(
        self,
        identity: TrustedIdentity,
        *,
        task_type: BackgroundTaskType,
        business_key: str,
        ordering_key: str,
        payload: dict[str, Any],
        source_turn_id: str | None = None,
        source_request_id: str | None = None,
        source_trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: int = 100,
        run_after: datetime | None = None,
        max_attempts: int = 5,
    ) -> BackgroundTaskRecord:
        """创建或恢复一条后台任务记录。

        :param identity: 任务来源可信身份范围。
        :param task_type: 后台任务类型。
        :param business_key: 任务业务幂等键。
        :param ordering_key: 任务顺序约束键。
        :param payload: 任务执行载荷。
        :param source_turn_id: 任务来源回合标识。
        :param source_request_id: 任务来源请求标识。
        :param source_trace_id: 任务来源追踪标识。
        :param metadata: 任务附加审计元数据。
        :param priority: 任务优先级。
        :param run_after: 任务最早可执行时间。
        :param max_attempts: 任务最大执行尝试次数。
        :return: 返回持久化后的后台任务记录。
        """
        ...

    def claim_due_tasks(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: float,
        task_types: tuple[BackgroundTaskType, ...] = (),
    ) -> tuple[BackgroundTaskRecord, ...]:
        """领取当前可执行的后台任务。

        :param worker_id: 当前 worker 标识。
        :param batch_size: 单次领取任务数量上限。
        :param lease_seconds: 任务租约时长。
        :param task_types: 可领取的任务类型白名单；为空时表示全部类型。
        :return: 返回已领取的后台任务记录元组。
        """
        ...

    def complete_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
    ) -> None:
        """将后台任务标记为成功完成。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param result: 任务执行结果摘要。
        :return: 无返回值。
        """
        ...

    def retry_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        error_type: str,
        error_message: str,
        retry_after_seconds: float,
        result: dict[str, Any] | None = None,
    ) -> None:
        """将后台任务标记为重试中并设置下一次执行时间。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param error_type: 最近一次失败的异常类型。
        :param error_message: 最近一次失败的异常描述。
        :param retry_after_seconds: 下一次允许重试的延迟秒数。
        :param result: 任务执行过程中的结构化结果摘要。
        :return: 无返回值。
        """
        ...

    def dead_letter_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        error_type: str,
        error_message: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """将后台任务标记为死信任务。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param error_type: 最近一次失败的异常类型。
        :param error_message: 最近一次失败的异常描述。
        :param result: 任务执行过程中的结构化结果摘要。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查后台任务仓储是否可访问。

        :return: 依赖表可访问时返回 True。
        """
        ...


@dataclass(frozen=True)
class _TaskRowSnapshot:
    """表示后台任务仓储内部使用的数据库行快照。

    :param task_type: 任务类型。
    :param status: 任务状态。
    :param payload: 任务载荷。
    :param result: 任务结果。
    :param last_error: 任务最近错误。
    :param metadata: 任务审计元数据。
    :param run_after: 任务最早执行时间。
    :param started_at: 任务开始时间。
    :param finished_at: 任务完成时间。
    :param locked_until: 任务租约过期时间。
    :return: 无返回值。
    """

    task_type: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    last_error: dict[str, Any] | None
    metadata: dict[str, Any]
    run_after: datetime
    started_at: datetime | None
    finished_at: datetime | None
    locked_until: datetime | None


class PostgresBackgroundTaskRepository(BackgroundTaskRepository):
    """基于 SQLAlchemy 的 PostgreSQL 持久化后台任务仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 后台任务仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def enqueue_task(
        self,
        identity: TrustedIdentity,
        *,
        task_type: BackgroundTaskType,
        business_key: str,
        ordering_key: str,
        payload: dict[str, Any],
        source_turn_id: str | None = None,
        source_request_id: str | None = None,
        source_trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: int = 100,
        run_after: datetime | None = None,
        max_attempts: int = 5,
    ) -> BackgroundTaskRecord:
        """创建或恢复一条 PostgreSQL 后台任务记录。

        :param identity: 任务来源可信身份范围。
        :param task_type: 后台任务类型。
        :param business_key: 任务业务幂等键。
        :param ordering_key: 任务顺序约束键。
        :param payload: 任务执行载荷。
        :param source_turn_id: 任务来源回合标识。
        :param source_request_id: 任务来源请求标识。
        :param source_trace_id: 任务来源追踪标识。
        :param metadata: 任务附加审计元数据。
        :param priority: 任务优先级。
        :param run_after: 任务最早可执行时间。
        :param max_attempts: 任务最大执行尝试次数。
        :return: 返回持久化后的后台任务记录。
        """
        now = datetime.now(UTC)
        task_id = f"bt_{uuid4().hex}"
        statement = pg_insert(BackgroundTaskModel).values(
            task_id=task_id,
            task_type=task_type.value,
            business_key=business_key,
            ordering_key=ordering_key,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            source_turn_id=source_turn_id,
            source_request_id=source_request_id,
            source_trace_id=source_trace_id,
            status=BackgroundTaskStatus.PENDING.value,
            priority=priority,
            run_after=run_after or now,
            attempt_count=0,
            max_attempts=max_attempts,
            locked_by=None,
            locked_until=None,
            payload=payload,
            result=None,
            last_error=None,
            metadata_json=metadata or {},
            started_at=None,
            finished_at=None,
            created_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_background_tasks_type_business_key",
            set_={
                "task_id": task_id,
                "ordering_key": ordering_key,
                "source_turn_id": source_turn_id,
                "source_request_id": source_request_id,
                "source_trace_id": source_trace_id,
                "status": BackgroundTaskStatus.PENDING.value,
                "priority": priority,
                "run_after": run_after or now,
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "locked_by": None,
                "locked_until": None,
                "payload": payload,
                "result": None,
                "last_error": None,
                "metadata": metadata or {},
                "started_at": None,
                "finished_at": None,
                "updated_at": now,
            },
            where=BackgroundTaskModel.status.in_(
                [BackgroundTaskStatus.DEAD_LETTER.value, BackgroundTaskStatus.CANCELLED.value]
            ),
        ).returning(*BackgroundTaskModel.__table__.c)
        try:
            with self.session_factory.begin() as session:
                row = session.execute(statement).mappings().first()
                if row is None:
                    row = self._fetch_by_business_key(session, task_type=task_type.value, business_key=business_key)
                if row is None:
                    raise BackgroundTaskRepositoryError("failed to persist background task")
                return self._record_from_row(row)
        except SQLAlchemyError as exc:
            raise BackgroundTaskRepositoryError("failed to enqueue background task") from exc

    def claim_due_tasks(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: float,
        task_types: tuple[BackgroundTaskType, ...] = (),
    ) -> tuple[BackgroundTaskRecord, ...]:
        """领取当前可执行的 PostgreSQL 后台任务。

        :param worker_id: 当前 worker 标识。
        :param batch_size: 单次领取任务数量上限。
        :param lease_seconds: 任务租约时长。
        :param task_types: 可领取的任务类型白名单；为空时表示全部类型。
        :return: 返回已领取的后台任务记录元组。
        """
        if batch_size <= 0:
            return ()
        claimed: list[BackgroundTaskRecord] = []
        lease_seconds = max(float(lease_seconds), 1.0)
        claimed_ordering_keys: set[str] = set()
        try:
            with self.session_factory.begin() as session:
                while len(claimed) < batch_size:
                    row = self._claim_one_task(
                        session,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                        task_types=task_types,
                        excluded_ordering_keys=claimed_ordering_keys,
                    )
                    if row is None:
                        break
                    claimed_ordering_keys.add(str(row["ordering_key"]))
                    claimed.append(self._record_from_row(row))
        except SQLAlchemyError as exc:
            raise BackgroundTaskRepositoryError("failed to claim background tasks") from exc
        return tuple(claimed)

    def complete_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
    ) -> None:
        """将 PostgreSQL 后台任务标记为成功完成。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param result: 任务执行结果摘要。
        :return: 无返回值。
        """
        self._transition_task(
            task_id,
            worker_id=worker_id,
            status=BackgroundTaskStatus.SUCCEEDED,
            result=result,
            last_error=None,
            run_after=None,
        )

    def retry_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        error_type: str,
        error_message: str,
        retry_after_seconds: float,
        result: dict[str, Any] | None = None,
    ) -> None:
        """将 PostgreSQL 后台任务标记为重试中并设置下一次执行时间。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param error_type: 最近一次失败的异常类型。
        :param error_message: 最近一次失败的异常描述。
        :param retry_after_seconds: 下一次允许重试的延迟秒数。
        :param result: 任务执行过程中的结构化结果摘要。
        :return: 无返回值。
        """
        retry_after_seconds = max(float(retry_after_seconds), 1.0)
        self._transition_task(
            task_id,
            worker_id=worker_id,
            status=BackgroundTaskStatus.RETRYING,
            result=result,
            last_error={"error_type": error_type, "error_message": error_message},
            run_after=datetime.now(UTC) + timedelta(seconds=retry_after_seconds),
        )

    def dead_letter_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        error_type: str,
        error_message: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """将 PostgreSQL 后台任务标记为死信任务。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param error_type: 最近一次失败的异常类型。
        :param error_message: 最近一次失败的异常描述。
        :param result: 任务执行过程中的结构化结果摘要。
        :return: 无返回值。
        """
        self._transition_task(
            task_id,
            worker_id=worker_id,
            status=BackgroundTaskStatus.DEAD_LETTER,
            result=result,
            last_error={"error_type": error_type, "error_message": error_message},
            run_after=None,
        )

    def is_ready(self) -> bool:
        """检查 PostgreSQL 后台任务表是否可访问。

        :return: 数据库查询成功时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(sa.select(BackgroundTaskModel.id).limit(1))
            return True
        except SQLAlchemyError:
            return False

    def _claim_one_task(
        self,
        session: Session,
        *,
        worker_id: str,
        lease_seconds: float,
        task_types: tuple[BackgroundTaskType, ...],
        excluded_ordering_keys: set[str],
    ) -> dict[str, Any] | None:
        """在已有事务中领取单条后台任务。

        :param session: 当前 SQLAlchemy 会话。
        :param worker_id: 当前 worker 标识。
        :param lease_seconds: 任务租约时长。
        :param task_types: 可领取的任务类型白名单。
        :param excluded_ordering_keys: 当前批次已领取的顺序约束键集合。
        :return: 返回行快照字典或空值。
        """
        filters: list[Any] = [
            BackgroundTaskModel.status.in_(
                [BackgroundTaskStatus.PENDING.value, BackgroundTaskStatus.RETRYING.value]
            ),
            BackgroundTaskModel.run_after <= sa.func.now(),
            sa.or_(
                BackgroundTaskModel.locked_until.is_(None),
                BackgroundTaskModel.locked_until < sa.func.now(),
            ),
            BackgroundTaskModel.attempt_count < BackgroundTaskModel.max_attempts,
            sa.func.pg_try_advisory_xact_lock(sa.func.hashtextextended(BackgroundTaskModel.ordering_key, 0)) == sa.true(),
        ]
        if task_types:
            filters.append(BackgroundTaskModel.task_type.in_([item.value for item in task_types]))
        if excluded_ordering_keys:
            filters.append(~BackgroundTaskModel.ordering_key.in_(sorted(excluded_ordering_keys)))
        statement = (
            sa.select(BackgroundTaskModel)
            .where(*filters)
            .order_by(BackgroundTaskModel.priority.asc(), BackgroundTaskModel.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = session.scalar(statement)
        if row is None:
            return None
        now = datetime.now(UTC)
        row.status = BackgroundTaskStatus.RUNNING.value
        row.locked_by = worker_id
        row.locked_until = now + timedelta(seconds=lease_seconds)
        row.started_at = row.started_at or now
        row.attempt_count = int(row.attempt_count) + 1
        row.updated_at = now
        session.flush()
        return self._row_to_mapping(row)

    def _transition_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: BackgroundTaskStatus,
        result: dict[str, Any] | None,
        last_error: dict[str, Any] | None,
        run_after: datetime | None,
    ) -> None:
        """在已有事务中更新单条后台任务状态。

        :param task_id: 后台任务标识。
        :param worker_id: 当前 worker 标识。
        :param status: 目标任务状态。
        :param result: 任务结果摘要。
        :param last_error: 任务最近错误摘要。
        :param run_after: 下一次可执行时间。
        :return: 无返回值。
        """
        values: dict[str, Any] = {
            "status": status.value,
            "locked_by": None,
            "locked_until": None,
            "updated_at": datetime.now(UTC),
            "finished_at": datetime.now(UTC) if status.is_terminal() else None,
        }
        if result is not None:
            values["result"] = result
        if last_error is not None:
            values["last_error"] = last_error
        if run_after is not None:
            values["run_after"] = run_after
        try:
            with self.session_factory.begin() as session:
                statement = (
                    sa.update(BackgroundTaskModel)
                    .where(
                        BackgroundTaskModel.task_id == task_id,
                        BackgroundTaskModel.locked_by == worker_id,
                    )
                    .values(**values)
                )
                session.execute(statement)
        except SQLAlchemyError as exc:
            raise BackgroundTaskRepositoryError("failed to transition background task state") from exc

    def _fetch_by_business_key(
        self,
        session: Session,
        *,
        task_type: str,
        business_key: str,
    ) -> dict[str, Any] | None:
        """读取指定任务类型和业务键对应的后台任务记录。

        :param session: 当前 SQLAlchemy 会话。
        :param task_type: 任务类型。
        :param business_key: 业务幂等键。
        :return: 返回行快照字典或空值。
        """
        row = session.execute(
            sa.select(BackgroundTaskModel).where(
                BackgroundTaskModel.task_type == task_type,
                BackgroundTaskModel.business_key == business_key,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._row_to_mapping(row)

    def _row_to_mapping(self, row: BackgroundTaskModel) -> dict[str, Any]:
        """将 SQLAlchemy 行对象转换为仓储内部快照字典。

        :param row: 后台任务数据表行。
        :return: 返回字典快照。
        """
        return {
            "task_id": row.task_id,
            "task_type": row.task_type,
            "business_key": row.business_key,
            "ordering_key": row.ordering_key,
            "user_id": row.user_id,
            "pet_id": row.pet_id,
            "session_id": row.session_id,
            "source_turn_id": row.source_turn_id,
            "source_request_id": row.source_request_id,
            "source_trace_id": row.source_trace_id,
            "status": row.status,
            "priority": row.priority,
            "run_after": row.run_after,
            "attempt_count": row.attempt_count,
            "max_attempts": row.max_attempts,
            "locked_by": row.locked_by,
            "locked_until": row.locked_until,
            "payload": dict(row.payload or {}),
            "result": dict(row.result) if isinstance(row.result, dict) else None,
            "last_error": dict(row.last_error) if isinstance(row.last_error, dict) else None,
            "metadata": dict(row.metadata_json or {}),
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _record_from_row(self, row: dict[str, Any]) -> BackgroundTaskRecord:
        """将仓储内部快照归一为对外任务记录对象。

        :param row: 后台任务行快照。
        :return: 返回结构化后台任务记录。
        :raises BackgroundTaskRepositoryError: 当任务类型或状态不合法时抛出。
        """
        try:
            task_type = BackgroundTaskType(str(row["task_type"]))
            status = BackgroundTaskStatus(str(row["status"]))
        except ValueError as exc:
            raise BackgroundTaskRepositoryError("invalid background task row state") from exc
        return BackgroundTaskRecord(
            task_id=str(row["task_id"]),
            task_type=task_type,
            business_key=str(row["business_key"]),
            ordering_key=str(row["ordering_key"]),
            user_id=str(row["user_id"]),
            pet_id=str(row["pet_id"]),
            session_id=str(row["session_id"]),
            source_turn_id=str(row["source_turn_id"]) if row.get("source_turn_id") is not None else None,
            source_request_id=str(row["source_request_id"]) if row.get("source_request_id") is not None else None,
            source_trace_id=str(row["source_trace_id"]) if row.get("source_trace_id") is not None else None,
            status=status,
            priority=int(row["priority"]),
            run_after=row["run_after"],
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            locked_by=str(row["locked_by"]) if row.get("locked_by") is not None else None,
            locked_until=row["locked_until"],
            payload=dict(row.get("payload") or {}),
            result=dict(row["result"]) if isinstance(row.get("result"), dict) else None,
            last_error=dict(row["last_error"]) if isinstance(row.get("last_error"), dict) else None,
            metadata=dict(row.get("metadata") or {}),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
