"""
文件：src/vet_agent/repositories/turn_execution.py
作用：提供 Agent 单回合执行门禁所需的 PostgreSQL 数据仓储契约与实现。
范围：仅本文件允许直接访问幂等记录数据表，并通过仓储协议向业务服务暴露幂等 claim、完成、失败标记与 turn lock 能力。
说明：本仓储属于“幂等与 turn lock”基础设施数据链，不承载临床安全、问诊状态、RAG、记忆写入等业务状态机。
说明：本文件位于 src-layout 下的 repositories 包内，跨包调用应通过 vet_agent.repositories 顶层导出。
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from vet_agent import TrustedIdentity
from vet_agent.db import IdempotencyRecordModel, make_engine, make_session_factory


class TurnIdempotencyClaimStatus(StrEnum):
    """表示幂等 claim 在 turn execution 数据链中的基础设施结果。

    :return: 无返回值。
    """

    CLAIMED = "claimed"
    REPLAYED = "replayed"
    PROCESSING = "processing"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class TurnIdempotencyClaim:
    """表示一次幂等 claim 的仓储层结果。

    :param status: claim 结果状态。
    :param response_snapshot: 可重放的首个成功响应快照。
    :param existing_request_hash: 已存在幂等记录的请求哈希。
    :return: 无返回值。
    """

    status: TurnIdempotencyClaimStatus
    response_snapshot: dict[str, Any] | None = None
    existing_request_hash: str | None = None


@dataclass(frozen=True)
class _IdempotencyRecordSnapshot:
    """表示仓储内部使用的幂等记录快照。

    :param status: 幂等记录状态。
    :param request_hash: 已保存请求语义哈希。
    :param response_snapshot: 可重放响应快照。
    :param updated_at: 幂等记录最近更新时间。
    :return: 无返回值。
    """

    status: str
    request_hash: str
    response_snapshot: dict[str, Any] | None
    updated_at: datetime | None


class TurnExecutionRepositoryError(RuntimeError):
    """表示 turn execution 仓储访问失败。

    :return: 无返回值。
    """


class TurnExecutionRepository(Protocol):
    """定义 Agent 单回合执行门禁的数据仓储协议。

    :return: 无返回值。
    """

    def turn_lock(self, identity: TrustedIdentity) -> AbstractAsyncContextManager[None]:
        """获取当前会话范围的 turn lock。

        :param identity: 本轮可信身份范围。
        :return: 返回异步上下文管理器，进入后表示同一会话范围的回合执行已串行化。
        """
        ...

    def claim_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        processing_ttl_seconds: float,
    ) -> TurnIdempotencyClaim:
        """尝试为当前回合声明幂等执行权。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param processing_ttl_seconds: processing 记录超过该时长后允许重新 claim。
        :return: 返回幂等 claim 结果。
        """
        ...

    def complete_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        """保存幂等回合的首个成功响应快照。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param response_snapshot: 首个成功响应快照。
        :return: 无返回值。
        """
        ...

    def fail_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        error_type: str,
    ) -> None:
        """标记幂等回合执行失败。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param error_type: 失败异常类型。
        :return: 无返回值。
        """
        ...

    def is_ready(self) -> bool:
        """检查 turn execution 仓储是否可用。

        :return: 数据库表可访问时返回 True。
        """
        ...


class PostgresTurnExecutionRepository(TurnExecutionRepository):
    """基于 PostgreSQL 的 Agent 单回合执行门禁仓储实现。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL turn execution 仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.engine: Engine = make_engine(database_url)
        self.session_factory = make_session_factory(database_url)

    @asynccontextmanager
    async def turn_lock(self, identity: TrustedIdentity) -> AsyncIterator[None]:
        """使用 PostgreSQL advisory lock 串行化同一会话范围的回合执行。

        :param identity: 本轮可信身份范围。
        :return: 返回异步上下文管理器，进入后持有当前范围的 turn lock。
        """
        lock_key = self._lock_key(identity)
        connection = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            # pg_advisory_lock 是 session-level 锁；这里持有连接但不持有空闲事务，
            # 避免长时间模型调用期间触发 PostgreSQL idle_in_transaction_session_timeout。
            connection.execute(select(func.pg_advisory_lock(lock_key)))
        except SQLAlchemyError as exc:
            _close_connection_quietly(connection)
            raise TurnExecutionRepositoryError("failed to manage PostgreSQL turn lock") from exc
        try:
            yield
        finally:
            try:
                connection.execute(select(func.pg_advisory_unlock(lock_key)))
            except SQLAlchemyError:
                # session-level advisory lock 会在连接关闭时由 PostgreSQL 自动释放；
                # 释放阶段的连接失效不应覆盖已经完成的 Agent 业务响应。
                pass
            finally:
                _close_connection_quietly(connection)

    def claim_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        processing_ttl_seconds: float,
    ) -> TurnIdempotencyClaim:
        """尝试声明幂等执行权或读取可重放响应。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param processing_ttl_seconds: processing 记录超过该时长后允许重新 claim。
        :return: 返回幂等 claim 结果。
        """
        try:
            inserted = self._insert_processing_idempotency(
                identity,
                idempotency_key=idempotency_key,
                request_id=request_id,
                trace_id=trace_id,
                request_hash=request_hash,
            )
            if inserted:
                return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.CLAIMED)

            record = self._get_idempotency_record(identity, idempotency_key)
            if record is None:
                return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.PROCESSING)

            if record.request_hash != request_hash:
                return TurnIdempotencyClaim(
                    status=TurnIdempotencyClaimStatus.CONFLICT,
                    existing_request_hash=record.request_hash,
                )

            if record.status == "completed" and record.response_snapshot:
                return TurnIdempotencyClaim(
                    status=TurnIdempotencyClaimStatus.REPLAYED,
                    response_snapshot=dict(record.response_snapshot),
                    existing_request_hash=record.request_hash,
                )

            if self._is_stale(record.updated_at, processing_ttl_seconds):
                self._claim_stale_idempotency(
                    identity,
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                    trace_id=trace_id,
                    request_hash=request_hash,
                )
                return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.CLAIMED)

            return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.PROCESSING)
        except SQLAlchemyError as exc:
            raise TurnExecutionRepositoryError("failed to claim idempotency record") from exc

    def complete_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        """保存当前幂等键对应的成功响应快照。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param response_snapshot: 首个成功响应快照。
        :return: 无返回值。
        """
        statement = pg_insert(IdempotencyRecordModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            trace_id=trace_id,
            request_hash=request_hash,
            response_id=response_snapshot.get("id"),
            status="completed",
            response_snapshot=response_snapshot,
            error_type=None,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_idempotency_scope_key",
            set_={
                "request_id": request_id,
                "trace_id": trace_id,
                "request_hash": request_hash,
                "response_id": response_snapshot.get("id"),
                "status": "completed",
                "response_snapshot": response_snapshot,
                "error_type": None,
                "updated_at": datetime.now(UTC),
            },
        )
        try:
            with self.session_factory.begin() as session:
                session.execute(statement)
        except SQLAlchemyError as exc:
            raise TurnExecutionRepositoryError("failed to complete idempotency record") from exc

    def fail_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        error_type: str,
    ) -> None:
        """标记当前幂等键对应的执行失败结果。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param error_type: 失败异常类型。
        :return: 无返回值。
        """
        statement = update(IdempotencyRecordModel).where(
            IdempotencyRecordModel.user_id == identity.user_id,
            IdempotencyRecordModel.pet_id == identity.pet_id,
            IdempotencyRecordModel.session_id == identity.session_id,
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        ).values(
            request_id=request_id,
            trace_id=trace_id,
            request_hash=request_hash,
            response_id=None,
            status="failed",
            response_snapshot=None,
            error_type=error_type,
            updated_at=datetime.now(UTC),
        )
        try:
            with self.session_factory.begin() as session:
                session.execute(statement)
        except SQLAlchemyError as exc:
            raise TurnExecutionRepositoryError("failed to fail idempotency record") from exc

    def is_ready(self) -> bool:
        """检查 PostgreSQL 幂等记录表是否可访问。

        :return: 数据库查询成功时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(select(IdempotencyRecordModel.id).limit(1))
            return True
        except SQLAlchemyError:
            return False

    def _insert_processing_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
    ) -> bool:
        """插入 processing 幂等记录以声明本轮执行权。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :return: 插入成功时返回 True，已存在冲突记录时返回 False。
        """
        statement = pg_insert(IdempotencyRecordModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            trace_id=trace_id,
            request_hash=request_hash,
            response_id=None,
            status="processing",
            response_snapshot=None,
            error_type=None,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_nothing(
            constraint="uq_idempotency_scope_key",
        ).returning(IdempotencyRecordModel.id)
        with self.session_factory.begin() as session:
            return session.scalar(statement) is not None

    def _get_idempotency_record(
        self,
        identity: TrustedIdentity,
        idempotency_key: str,
    ) -> _IdempotencyRecordSnapshot | None:
        """读取当前范围和幂等键对应的幂等记录。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :return: 存在记录时返回仓储内部快照，否则返回 None。
        """
        with self.session_factory() as session:
            row = session.scalar(
                select(IdempotencyRecordModel).where(
                    IdempotencyRecordModel.user_id == identity.user_id,
                    IdempotencyRecordModel.pet_id == identity.pet_id,
                    IdempotencyRecordModel.session_id == identity.session_id,
                    IdempotencyRecordModel.idempotency_key == idempotency_key,
                )
            )
        if row is None:
            return None
        response_snapshot = row.response_snapshot if isinstance(row.response_snapshot, dict) else None
        return _IdempotencyRecordSnapshot(
            status=row.status,
            request_hash=row.request_hash,
            response_snapshot=dict(response_snapshot) if response_snapshot is not None else None,
            updated_at=row.updated_at,
        )

    def _claim_stale_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
    ) -> None:
        """重新声明已过期 processing 或 failed 幂等记录的执行权。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :return: 无返回值。
        """
        statement = update(IdempotencyRecordModel).where(
            IdempotencyRecordModel.user_id == identity.user_id,
            IdempotencyRecordModel.pet_id == identity.pet_id,
            IdempotencyRecordModel.session_id == identity.session_id,
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        ).values(
            request_id=request_id,
            trace_id=trace_id,
            request_hash=request_hash,
            response_id=None,
            status="processing",
            response_snapshot=None,
            error_type=None,
            updated_at=datetime.now(UTC),
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    def _is_stale(self, updated_at: datetime | None, ttl_seconds: float) -> bool:
        """判断 processing 或 failed 幂等记录是否已超过可重新声明时间。

        :param updated_at: 幂等记录最近更新时间。
        :param ttl_seconds: processing 记录超过该时长后允许重新 claim。
        :return: 已过期时返回 True。
        """
        if updated_at is None:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated_at).total_seconds() > ttl_seconds

    def _lock_key(self, identity: TrustedIdentity) -> int:
        """根据可信身份范围生成 PostgreSQL advisory lock 键。

        :param identity: 本轮可信身份范围。
        :return: 返回 PostgreSQL bigint 范围内的锁键。
        """
        raw = f"{identity.user_id}:{identity.pet_id}:{identity.session_id}".encode("utf-8")
        value = int.from_bytes(hashlib.sha256(raw).digest()[:8], byteorder="big", signed=False)
        return value - (1 << 63)


def _close_connection_quietly(connection: Connection) -> None:
    """安静关闭 turn lock 专用数据库连接。

    :param connection: turn lock 持有期间独占使用的 PostgreSQL 连接。
    :return: 无返回值。
    """
    try:
        connection.close()
    except SQLAlchemyError:
        # 连接已由服务端关闭时，session-level advisory lock 已随会话结束释放；
        # 关闭阶段不应覆盖主业务链路已经生成的结果。
        pass
