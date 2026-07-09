##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_version.py
# 作用: 提供基于 SQLAlchemy Core 的 checkpoint thread 版本推进与乐观锁仓储。
# 边界: 仅更新 checkpoint_thread.latest_version/latest_checkpoint_id/status；不写 LangGraph checkpoint。
##################################################################################################

from datetime import UTC, datetime

from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SqlAlchemyTimeoutError

from veterinary_agent.checkpoint_store.dto import (
    SaveCheckpointCommandDto,
    SaveCheckpointResultDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.sqlalchemy_tables import CHECKPOINT_THREAD_TABLE


def _checkpoint_status_to_thread_status(status: CheckpointRecordStatus) -> str:
    """将 checkpoint 快照状态映射为 checkpoint thread 状态。

    :param status: 本次保存的 checkpoint 快照状态。
    :return: 可写入 checkpoint_thread.status 的状态字符串。
    """

    return status.value


def _build_thread_not_found_error(
    *,
    command: SaveCheckpointCommandDto,
) -> CheckpointStoreError:
    """构建 SaveCheckpoint 版本推进时的 thread 不存在错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message="checkpoint thread 不存在，无法推进版本",
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=False,
        conflict_with={"thread_id": command.thread_id},
    )


def _build_version_conflict_error(
    *,
    command: SaveCheckpointCommandDto,
    actual_version: int,
    latest_checkpoint_id: str | None,
) -> CheckpointStoreError:
    """构建 checkpoint thread 版本冲突错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :param actual_version: 数据库中当前最新版本。
    :param latest_checkpoint_id: 数据库中当前最新 checkpoint ID。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message="checkpoint thread latest_version 与 expected_version 不一致",
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=True,
        conflict_with={
            "thread_id": command.thread_id,
            "expected_version": command.expected_version,
            "actual_version": actual_version,
            "latest_checkpoint_id": latest_checkpoint_id,
        },
    )


def _build_session_mismatch_error(
    *,
    command: SaveCheckpointCommandDto,
    actual_session_id: str,
) -> CheckpointStoreError:
    """构建 checkpoint thread 与请求 session_id 不一致错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :param actual_session_id: 数据库中 thread 绑定的 session ID。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message="checkpoint thread 已绑定到不同 session_id",
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=False,
        conflict_with={
            "thread_id": command.thread_id,
            "expected_session_id": command.session_id,
            "actual_session_id": actual_session_id,
        },
    )


def _build_invalid_argument_error(
    *,
    command: SaveCheckpointCommandDto,
    message: str,
) -> CheckpointStoreError:
    """构建版本推进非法参数错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message=message,
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=False,
    )


def _build_store_unavailable_error(
    *,
    command: SaveCheckpointCommandDto,
    message: str,
) -> CheckpointStoreError:
    """构建版本推进存储不可用错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message=message,
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=True,
    )


def _build_timeout_error(
    *,
    command: SaveCheckpointCommandDto,
    message: str,
) -> CheckpointStoreError:
    """构建版本推进数据库超时错误。

    :param command: 保存 checkpoint 的命令 DTO。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
        operation=CheckpointOperation.SAVE_CHECKPOINT,
        message=message,
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=True,
    )


class SqlAlchemyCheckpointVersionRepository:
    """基于 SQLAlchemy Core 的 checkpoint thread 版本仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 checkpoint thread 版本仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def advance_thread_version(
        self,
        *,
        command: SaveCheckpointCommandDto,
        checkpoint_id: str,
        state_size_bytes: int,
    ) -> SaveCheckpointResultDto:
        """按 expected_version 原子推进 checkpoint thread 版本。

        :param command: 保存 checkpoint 的命令 DTO。
        :param checkpoint_id: 已保存或待关联的 checkpoint ID。
        :param state_size_bytes: checkpoint 状态体序列化字节数。
        :return: 版本推进成功后的保存结果 DTO。
        :raises CheckpointStoreError: 当版本冲突、thread 不存在、参数非法或存储不可用时抛出。
        """

        self._validate_advance_arguments(
            command=command,
            checkpoint_id=checkpoint_id,
            state_size_bytes=state_size_bytes,
        )
        try:
            return self._advance_thread_version_once(
                command=command,
                checkpoint_id=checkpoint_id,
                state_size_bytes=state_size_bytes,
            )
        except CheckpointStoreError:
            raise
        except SqlAlchemyTimeoutError as exc:
            raise _build_timeout_error(
                command=command,
                message="checkpoint thread 版本推进数据库操作超时",
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_store_unavailable_error(
                command=command,
                message="checkpoint thread 版本推进数据库操作失败",
            ) from exc

    def _validate_advance_arguments(
        self,
        *,
        command: SaveCheckpointCommandDto,
        checkpoint_id: str,
        state_size_bytes: int,
    ) -> None:
        """校验版本推进额外参数。

        :param command: 保存 checkpoint 的命令 DTO。
        :param checkpoint_id: 已保存或待关联的 checkpoint ID。
        :param state_size_bytes: checkpoint 状态体序列化字节数。
        :return: None。
        :raises CheckpointStoreError: 当 checkpoint_id 为空或状态体大小非法时抛出。
        """

        if not checkpoint_id.strip():
            raise _build_invalid_argument_error(
                command=command,
                message="checkpoint_id 不得为空",
            )
        if state_size_bytes < 0:
            raise _build_invalid_argument_error(
                command=command,
                message="state_size_bytes 不得为负数",
            )

    def _advance_thread_version_once(
        self,
        *,
        command: SaveCheckpointCommandDto,
        checkpoint_id: str,
        state_size_bytes: int,
    ) -> SaveCheckpointResultDto:
        """执行一次 checkpoint thread 版本推进事务。

        :param command: 保存 checkpoint 的命令 DTO。
        :param checkpoint_id: 已保存或待关联的 checkpoint ID。
        :param state_size_bytes: checkpoint 状态体序列化字节数。
        :return: 版本推进成功后的保存结果 DTO。
        :raises CheckpointStoreError: 当 thread 不存在、session 不一致或版本冲突时抛出。
        """

        now = datetime.now(UTC)
        new_version = command.expected_version + 1
        with self._engine.begin() as connection:
            result = connection.execute(
                CHECKPOINT_THREAD_TABLE.update()
                .where(CHECKPOINT_THREAD_TABLE.c.thread_id == command.thread_id)
                .where(CHECKPOINT_THREAD_TABLE.c.session_id == command.session_id)
                .where(
                    CHECKPOINT_THREAD_TABLE.c.latest_version
                    == command.expected_version
                )
                .values(
                    latest_version=new_version,
                    latest_checkpoint_id=checkpoint_id,
                    status=_checkpoint_status_to_thread_status(command.status),
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                return SaveCheckpointResultDto(
                    checkpoint_id=checkpoint_id,
                    thread_id=command.thread_id,
                    new_version=new_version,
                    status=command.status,
                    state_size_bytes=state_size_bytes,
                    saved_at=now,
                )
            thread_row = self._fetch_thread_by_id(
                connection=connection,
                thread_id=command.thread_id,
            )
            self._raise_after_update_miss(command=command, thread_row=thread_row)
            raise _build_store_unavailable_error(
                command=command,
                message="checkpoint thread 版本推进未命中且未能识别原因",
            )

    def _fetch_thread_by_id(
        self,
        *,
        connection: Connection,
        thread_id: str,
    ) -> RowMapping | None:
        """按 thread_id 精确读取 checkpoint thread。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param thread_id: 需要读取的 checkpoint thread ID。
        :return: 命中的 checkpoint_thread 行；不存在时返回 None。
        """

        return (
            connection.execute(
                CHECKPOINT_THREAD_TABLE.select().where(
                    CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id
                )
            )
            .mappings()
            .one_or_none()
        )

    def _raise_after_update_miss(
        self,
        *,
        command: SaveCheckpointCommandDto,
        thread_row: RowMapping | None,
    ) -> None:
        """根据版本推进更新未命中的原因抛出领域错误。

        :param command: 保存 checkpoint 的命令 DTO。
        :param thread_row: 按 thread_id 重读到的 checkpoint_thread 行；不存在时为空。
        :return: None。
        :raises CheckpointStoreError: 始终根据未命中原因抛出领域错误。
        """

        if thread_row is None:
            raise _build_thread_not_found_error(command=command)
        actual_session_id = str(thread_row["session_id"])
        if actual_session_id != command.session_id:
            raise _build_session_mismatch_error(
                command=command,
                actual_session_id=actual_session_id,
            )
        raise _build_version_conflict_error(
            command=command,
            actual_version=int(thread_row["latest_version"]),
            latest_checkpoint_id=(
                None
                if thread_row["latest_checkpoint_id"] is None
                else str(thread_row["latest_checkpoint_id"])
            ),
        )


__all__: tuple[str, ...] = ("SqlAlchemyCheckpointVersionRepository",)
