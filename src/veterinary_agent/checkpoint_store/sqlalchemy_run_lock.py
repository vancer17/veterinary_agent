##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_run_lock.py
# 作用: 提供基于 SQLAlchemy Core 的 CheckpointStore 运行锁控制面仓储。
# 边界: 仅实现 checkpoint_run_lock 与 checkpoint_thread 的运行互斥状态更新；不调用 LangGraph。
##################################################################################################

from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from veterinary_agent.checkpoint_store.dto import (
    AcquireRunLockCommandDto,
    AcquireRunLockResultDto,
    ReleaseRunLockCommandDto,
    ReleaseRunLockResultDto,
    SaveCheckpointCommandDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointThreadStatus,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.sqlalchemy_tables import (
    CHECKPOINT_RUN_LOCK_TABLE,
    CHECKPOINT_THREAD_TABLE,
)


def _normalize_datetime(value: datetime) -> datetime:
    """将数据库返回的 datetime 归一为 UTC aware datetime。

    :param value: 数据库读取到的时间值。
    :return: 带 UTC 时区信息的 datetime。
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _build_thread_not_found_error(
    *,
    operation: CheckpointOperation,
    request_id: str,
    trace_id: str,
    thread_id: str,
) -> CheckpointStoreError:
    """构建运行锁操作中的 thread 不存在错误。

    :param operation: 当前 CheckpointStore 操作名。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param thread_id: 未找到的 checkpoint thread ID。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND,
        operation=operation,
        message="checkpoint thread 不存在，无法处理运行锁",
        request_id=request_id,
        trace_id=trace_id,
        retryable=False,
        conflict_with={"thread_id": thread_id},
    )


def _build_lock_store_unavailable_error(
    *,
    operation: CheckpointOperation,
    request_id: str,
    trace_id: str,
    message: str,
) -> CheckpointStoreError:
    """构建运行锁存储不可用错误。

    :param operation: 当前 CheckpointStore 操作名。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=True,
    )


def _build_lock_timeout_error(
    *,
    operation: CheckpointOperation,
    request_id: str,
    trace_id: str,
    message: str,
) -> CheckpointStoreError:
    """构建运行锁数据库超时错误。

    :param operation: 当前 CheckpointStore 操作名。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=True,
    )


class SqlAlchemyCheckpointRunLockRepository:
    """基于 SQLAlchemy Core 的 checkpoint_run_lock 仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 checkpoint_run_lock 仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def acquire_run_lock(
        self,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """获取同一 thread 的运行互斥锁。

        :param command: 获取运行锁的命令 DTO。
        :return: 运行锁获取结果。
        :raises CheckpointStoreError: 当锁被其他 run 持有、thread 不存在或存储不可用时抛出。
        """

        try:
            try:
                return self._acquire_run_lock_once(command=command)
            except IntegrityError:
                return self._acquire_run_lock_after_insert_conflict(command=command)
        except CheckpointStoreError:
            raise
        except SqlAlchemyTimeoutError as exc:
            raise _build_lock_timeout_error(
                operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="AcquireRunLock 数据库操作超时",
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_lock_store_unavailable_error(
                operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="AcquireRunLock 数据库操作失败",
            ) from exc

    def release_run_lock(
        self,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """释放当前 run 持有的运行锁。

        :param command: 释放运行锁的命令 DTO。
        :return: 运行锁释放结果。
        :raises CheckpointStoreError: 当释放者不是锁持有者、thread 不存在或存储不可用时抛出。
        """

        try:
            return self._release_run_lock_once(command=command)
        except CheckpointStoreError:
            raise
        except SqlAlchemyTimeoutError as exc:
            raise _build_lock_timeout_error(
                operation=CheckpointOperation.RELEASE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="ReleaseRunLock 数据库操作超时",
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_lock_store_unavailable_error(
                operation=CheckpointOperation.RELEASE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="ReleaseRunLock 数据库操作失败",
            ) from exc

    def ensure_run_lock_held(
        self,
        command: SaveCheckpointCommandDto,
    ) -> None:
        """确认 SaveCheckpoint 调用方仍持有有效运行锁。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 thread 不存在、未持锁、锁已过期或存储不可用时抛出。
        """

        try:
            self._ensure_run_lock_held_once(command=command)
        except CheckpointStoreError:
            raise
        except SqlAlchemyTimeoutError as exc:
            raise _build_lock_timeout_error(
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="SaveCheckpoint 运行锁校验数据库操作超时",
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_lock_store_unavailable_error(
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="SaveCheckpoint 运行锁校验数据库操作失败",
            ) from exc

    def _acquire_run_lock_once(
        self,
        *,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """执行一次运行锁获取事务。

        :param command: 获取运行锁的命令 DTO。
        :return: 运行锁获取结果。
        :raises IntegrityError: 当并发创建运行锁命中唯一约束时抛出。
        :raises CheckpointStoreError: 当 thread 不存在或锁被其他 run 持有时抛出。
        """

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=command.lock_ttl_seconds)
        with self._engine.begin() as connection:
            self._ensure_thread_exists(
                connection=connection,
                operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                thread_id=command.thread_id,
            )
            lock_row = self._fetch_lock_by_thread_id(
                connection=connection,
                thread_id=command.thread_id,
                for_update=True,
            )
            if lock_row is None:
                return self._insert_new_lock(
                    connection=connection,
                    command=command,
                    now=now,
                    expires_at=expires_at,
                )
            return self._resolve_existing_lock_for_acquire(
                connection=connection,
                command=command,
                lock_row=lock_row,
                now=now,
                expires_at=expires_at,
            )

    def _acquire_run_lock_after_insert_conflict(
        self,
        *,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """处理并发插入运行锁导致的主键冲突。

        :param command: 获取运行锁的命令 DTO。
        :return: 冲突后重读并按现有锁状态处理后的结果。
        :raises CheckpointStoreError: 当 thread 不存在、锁缺失异常或锁被其他 run 持有时抛出。
        """

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=command.lock_ttl_seconds)
        with self._engine.begin() as connection:
            self._ensure_thread_exists(
                connection=connection,
                operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                thread_id=command.thread_id,
            )
            lock_row = self._fetch_lock_by_thread_id(
                connection=connection,
                thread_id=command.thread_id,
                for_update=True,
            )
            if lock_row is None:
                raise _build_lock_store_unavailable_error(
                    operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    message="运行锁插入冲突后无法读取既有锁",
                )
            return self._resolve_existing_lock_for_acquire(
                connection=connection,
                command=command,
                lock_row=lock_row,
                now=now,
                expires_at=expires_at,
            )

    def _release_run_lock_once(
        self,
        *,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """执行一次运行锁释放事务。

        :param command: 释放运行锁的命令 DTO。
        :return: 运行锁释放结果。
        :raises CheckpointStoreError: 当 thread 不存在或释放者不是锁持有者时抛出。
        """

        with self._engine.begin() as connection:
            self._ensure_thread_exists(
                connection=connection,
                operation=CheckpointOperation.RELEASE_RUN_LOCK,
                request_id=command.request_id,
                trace_id=command.trace_id,
                thread_id=command.thread_id,
            )
            lock_row = self._fetch_lock_by_thread_id(
                connection=connection,
                thread_id=command.thread_id,
                for_update=True,
            )
            if lock_row is None:
                return ReleaseRunLockResultDto(
                    thread_id=command.thread_id,
                    run_id=command.run_id,
                    released=False,
                    idempotent=True,
                )
            lock_run_id = str(lock_row["run_id"])
            if lock_run_id != command.run_id:
                self._raise_lock_owner_mismatch(
                    command=command,
                    locked_by_run_id=lock_run_id,
                )
            connection.execute(
                CHECKPOINT_RUN_LOCK_TABLE.delete().where(
                    CHECKPOINT_RUN_LOCK_TABLE.c.thread_id == command.thread_id
                )
            )
            self._touch_thread(
                connection=connection,
                thread_id=command.thread_id,
                now=datetime.now(UTC),
            )
            return ReleaseRunLockResultDto(
                thread_id=command.thread_id,
                run_id=command.run_id,
                released=True,
                idempotent=False,
            )

    def _ensure_run_lock_held_once(
        self,
        *,
        command: SaveCheckpointCommandDto,
    ) -> None:
        """执行一次 SaveCheckpoint 运行锁校验事务。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 thread 不存在、未持锁、锁已过期或锁属于其他 run 时抛出。
        """

        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            self._ensure_thread_exists(
                connection=connection,
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                request_id=command.request_id,
                trace_id=command.trace_id,
                thread_id=command.thread_id,
            )
            lock_row = self._fetch_lock_by_thread_id(
                connection=connection,
                thread_id=command.thread_id,
                for_update=True,
            )
            if lock_row is None:
                self._raise_save_lock_owner_mismatch(
                    command=command,
                    locked_by_run_id=None,
                    reason="lock_missing",
                )
                return
            lock_run_id = str(lock_row["run_id"])
            if lock_run_id != command.run_id:
                self._raise_save_lock_owner_mismatch(
                    command=command,
                    locked_by_run_id=lock_run_id,
                    reason="locked_by_other_run",
                )
            lock_expires_at = _normalize_datetime(lock_row["expires_at"])
            if lock_expires_at <= now:
                self._raise_save_lock_owner_mismatch(
                    command=command,
                    locked_by_run_id=lock_run_id,
                    reason="lock_expired",
                    expires_at=lock_expires_at,
                )

    def _ensure_thread_exists(
        self,
        *,
        connection: Connection,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
        thread_id: str,
    ) -> None:
        """确认 checkpoint thread 存在。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param thread_id: 需要检查的 checkpoint thread ID。
        :return: None。
        :raises CheckpointStoreError: 当 thread 不存在时抛出。
        """

        statement = CHECKPOINT_THREAD_TABLE.select().where(
            CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id
        )
        if connection.execute(statement).mappings().one_or_none() is not None:
            return
        raise _build_thread_not_found_error(
            operation=operation,
            request_id=request_id,
            trace_id=trace_id,
            thread_id=thread_id,
        )

    def _fetch_lock_by_thread_id(
        self,
        *,
        connection: Connection,
        thread_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        """按 thread_id 精确读取运行锁。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param thread_id: 需要读取运行锁的 checkpoint thread ID。
        :param for_update: 是否请求数据库对命中的锁行加行级锁。
        :return: 命中的 checkpoint_run_lock 行；不存在时返回 None。
        """

        statement = CHECKPOINT_RUN_LOCK_TABLE.select().where(
            CHECKPOINT_RUN_LOCK_TABLE.c.thread_id == thread_id
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one_or_none()

    def _insert_new_lock(
        self,
        *,
        connection: Connection,
        command: AcquireRunLockCommandDto,
        now: datetime,
        expires_at: datetime,
    ) -> AcquireRunLockResultDto:
        """插入新的运行锁。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 获取运行锁的命令 DTO。
        :param now: 本次事务使用的当前时间。
        :param expires_at: 新运行锁过期时间。
        :return: 运行锁获取结果。
        :raises IntegrityError: 当并发插入命中主键冲突时抛出。
        """

        connection.execute(
            CHECKPOINT_RUN_LOCK_TABLE.insert().values(
                thread_id=command.thread_id,
                run_id=command.run_id,
                expires_at=expires_at,
                acquired_at=now,
                updated_at=now,
            )
        )
        self._mark_thread_running(
            connection=connection,
            thread_id=command.thread_id,
            now=now,
        )
        return AcquireRunLockResultDto(
            thread_id=command.thread_id,
            run_id=command.run_id,
            lock_acquired=True,
            expires_at=expires_at,
            idempotent=False,
            stale_lock_replaced=False,
        )

    def _resolve_existing_lock_for_acquire(
        self,
        *,
        connection: Connection,
        command: AcquireRunLockCommandDto,
        lock_row: RowMapping,
        now: datetime,
        expires_at: datetime,
    ) -> AcquireRunLockResultDto:
        """根据既有运行锁状态处理获取请求。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 获取运行锁的命令 DTO。
        :param lock_row: 已通过 thread_id 命中的 checkpoint_run_lock 行。
        :param now: 本次事务使用的当前时间。
        :param expires_at: 当前请求希望写入的锁过期时间。
        :return: 运行锁获取结果。
        :raises CheckpointStoreError: 当其他 run 持有未过期锁时抛出。
        """

        lock_run_id = str(lock_row["run_id"])
        lock_expires_at = _normalize_datetime(lock_row["expires_at"])
        if lock_run_id == command.run_id:
            return self._refresh_same_run_lock(
                connection=connection,
                command=command,
                now=now,
                expires_at=expires_at,
            )
        if lock_expires_at > now:
            self._raise_locked(
                command=command,
                locked_by_run_id=lock_run_id,
                locked_until=lock_expires_at,
            )
        return self._replace_stale_lock(
            connection=connection,
            command=command,
            now=now,
            expires_at=expires_at,
        )

    def _refresh_same_run_lock(
        self,
        *,
        connection: Connection,
        command: AcquireRunLockCommandDto,
        now: datetime,
        expires_at: datetime,
    ) -> AcquireRunLockResultDto:
        """刷新同一 run 已持有的运行锁。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 获取运行锁的命令 DTO。
        :param now: 本次事务使用的当前时间。
        :param expires_at: 刷新后的锁过期时间。
        :return: 幂等获取运行锁结果。
        """

        connection.execute(
            CHECKPOINT_RUN_LOCK_TABLE.update()
            .where(CHECKPOINT_RUN_LOCK_TABLE.c.thread_id == command.thread_id)
            .values(
                expires_at=expires_at,
                updated_at=now,
            )
        )
        self._mark_thread_running(
            connection=connection,
            thread_id=command.thread_id,
            now=now,
        )
        return AcquireRunLockResultDto(
            thread_id=command.thread_id,
            run_id=command.run_id,
            lock_acquired=True,
            expires_at=expires_at,
            idempotent=True,
            stale_lock_replaced=False,
        )

    def _replace_stale_lock(
        self,
        *,
        connection: Connection,
        command: AcquireRunLockCommandDto,
        now: datetime,
        expires_at: datetime,
    ) -> AcquireRunLockResultDto:
        """替换其他 run 留下的过期运行锁。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 获取运行锁的命令 DTO。
        :param now: 本次事务使用的当前时间。
        :param expires_at: 新运行锁过期时间。
        :return: 过期锁替换后的获取结果。
        """

        connection.execute(
            CHECKPOINT_RUN_LOCK_TABLE.update()
            .where(CHECKPOINT_RUN_LOCK_TABLE.c.thread_id == command.thread_id)
            .values(
                run_id=command.run_id,
                expires_at=expires_at,
                acquired_at=now,
                updated_at=now,
            )
        )
        self._mark_thread_running(
            connection=connection,
            thread_id=command.thread_id,
            now=now,
        )
        return AcquireRunLockResultDto(
            thread_id=command.thread_id,
            run_id=command.run_id,
            lock_acquired=True,
            expires_at=expires_at,
            idempotent=False,
            stale_lock_replaced=True,
        )

    def _mark_thread_running(
        self,
        *,
        connection: Connection,
        thread_id: str,
        now: datetime,
    ) -> None:
        """将 thread 标记为 running。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param thread_id: 需要更新的 checkpoint thread ID。
        :param now: 本次事务使用的当前时间。
        :return: None。
        """

        connection.execute(
            CHECKPOINT_THREAD_TABLE.update()
            .where(CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id)
            .values(
                status=CheckpointThreadStatus.RUNNING.value,
                updated_at=now,
            )
        )

    def _touch_thread(
        self,
        *,
        connection: Connection,
        thread_id: str,
        now: datetime,
    ) -> None:
        """刷新 thread 更新时间。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param thread_id: 需要更新的 checkpoint thread ID。
        :param now: 本次事务使用的当前时间。
        :return: None。
        """

        connection.execute(
            CHECKPOINT_THREAD_TABLE.update()
            .where(CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id)
            .values(updated_at=now)
        )

    def _raise_locked(
        self,
        *,
        command: AcquireRunLockCommandDto,
        locked_by_run_id: str,
        locked_until: datetime,
    ) -> None:
        """抛出运行锁已被其他 run 持有错误。

        :param command: 获取运行锁的命令 DTO。
        :param locked_by_run_id: 当前持锁的 run ID。
        :param locked_until: 当前锁过期时间。
        :return: None。
        :raises CheckpointStoreError: 始终抛出 CHECKPOINT_LOCKED。
        """

        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_LOCKED,
            operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
            message="checkpoint thread 运行锁已被其他 run 持有",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=True,
            conflict_with={
                "thread_id": command.thread_id,
                "locked_by_run_id": locked_by_run_id,
                "requested_run_id": command.run_id,
                "expires_at": locked_until.isoformat(),
            },
        )

    def _raise_lock_owner_mismatch(
        self,
        *,
        command: ReleaseRunLockCommandDto,
        locked_by_run_id: str,
    ) -> None:
        """抛出释放者不是当前锁持有者错误。

        :param command: 释放运行锁的命令 DTO。
        :param locked_by_run_id: 当前持锁的 run ID。
        :return: None。
        :raises CheckpointStoreError: 始终抛出 CHECKPOINT_LOCK_OWNER_MISMATCH。
        """

        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_LOCK_OWNER_MISMATCH,
            operation=CheckpointOperation.RELEASE_RUN_LOCK,
            message="checkpoint thread 运行锁不能由非持有者释放",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "locked_by_run_id": locked_by_run_id,
                "requested_run_id": command.run_id,
            },
        )

    def _raise_save_lock_owner_mismatch(
        self,
        *,
        command: SaveCheckpointCommandDto,
        locked_by_run_id: str | None,
        reason: str,
        expires_at: datetime | None = None,
    ) -> None:
        """抛出 SaveCheckpoint 调用方未持有有效运行锁错误。

        :param command: 保存 checkpoint 的命令 DTO。
        :param locked_by_run_id: 当前锁持有者 run ID；无锁时为空。
        :param reason: 锁校验失败原因。
        :param expires_at: 可选当前锁过期时间。
        :return: None。
        :raises CheckpointStoreError: 始终抛出 CHECKPOINT_LOCK_OWNER_MISMATCH。
        """

        conflict_with: dict[str, object] = {
            "thread_id": command.thread_id,
            "locked_by_run_id": locked_by_run_id,
            "requested_run_id": command.run_id,
            "reason": reason,
        }
        if expires_at is not None:
            conflict_with["expires_at"] = expires_at.isoformat()
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_LOCK_OWNER_MISMATCH,
            operation=CheckpointOperation.SAVE_CHECKPOINT,
            message="SaveCheckpoint 调用方未持有有效运行锁",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with=conflict_with,
        )


__all__: tuple[str, ...] = ("SqlAlchemyCheckpointRunLockRepository",)
