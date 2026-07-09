##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_store.py
# 作用: 提供基于 SQLAlchemy 的 CheckpointStore 控制面 facade，装配 thread、运行锁、
#       checkpoint 读取与 segment 发布幂等仓储。
# 边界: 仅访问项目级 checkpoint 控制面表；不访问 LangGraph checkpoint 内部表、不实现 GraphRuntime。
##################################################################################################

import asyncio
import json
from datetime import UTC, datetime
from typing import Awaitable, NoReturn, TypeVar

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from veterinary_agent.checkpoint_store.dto import (
    AcquireRunLockCommandDto,
    AcquireRunLockResultDto,
    CheckpointSummaryDto,
    CheckpointThreadDto,
    EnsureThreadCommandDto,
    EnsureThreadResultDto,
    GetCheckpointQueryDto,
    ListCheckpointsQueryDto,
    ListCheckpointsResultDto,
    LoadLatestCheckpointQueryDto,
    LoadLatestCheckpointResultDto,
    LoadSessionStateQueryDto,
    LoadSessionStateResultDto,
    MarkSegmentPublishedCommandDto,
    MarkSegmentPublishedResultDto,
    ReleaseRunLockCommandDto,
    ReleaseRunLockResultDto,
    SaveCheckpointCommandDto,
    SaveCheckpointResultDto,
    SessionBusinessStateDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointThreadStatus,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.checkpoint_mapper import CheckpointTupleMapper
from veterinary_agent.checkpoint_store.langgraph_reader import LangGraphCheckpointReader
from veterinary_agent.checkpoint_store.langgraph_writer import LangGraphCheckpointWriter
from veterinary_agent.checkpoint_store.sqlalchemy_read import (
    SqlAlchemyCheckpointReadRepository,
)
from veterinary_agent.checkpoint_store.sqlalchemy_run_lock import (
    SqlAlchemyCheckpointRunLockRepository,
)
from veterinary_agent.checkpoint_store.sqlalchemy_segment_publish import (
    SqlAlchemyCheckpointSegmentPublishRepository,
)
from veterinary_agent.checkpoint_store.sqlalchemy_tables import (
    CHECKPOINT_RUN_LOCK_TABLE,
    CHECKPOINT_SEGMENT_PUBLISH_TABLE,
    CHECKPOINT_STORE_METADATA,
    CHECKPOINT_THREAD_TABLE,
)
from veterinary_agent.checkpoint_store.sqlalchemy_version import (
    SqlAlchemyCheckpointVersionRepository,
)
from veterinary_agent.checkpoint_store.store import TodoCheckpointStore
from veterinary_agent.checkpoint_store.thread_ids import build_checkpoint_thread_id
from veterinary_agent.config import (
    CheckpointStoreSettings,
    load_checkpoint_store_settings,
)

_T = TypeVar("_T")


def _measure_json_bytes(value: object) -> int:
    """计算 JSON 值序列化后的 UTF-8 字节数。

    :param value: 需要计算大小的 JSON 兼容值。
    :return: 序列化后的 UTF-8 字节数。
    :raises TypeError: 当值无法被 JSON 序列化时抛出。
    :raises ValueError: 当值包含 JSON 不支持的浮点特殊值时抛出。
    """

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(encoded.encode("utf-8"))


def _build_runtime_config_error(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 RuntimeConfig 约束相关的 CheckpointStore 领域错误。

    :param code: CheckpointStore 稳定错误码。
    :param operation: 当前 CheckpointStore 操作名。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 可选重试策略覆盖。
    :param conflict_with: 可选冲突对象摘要。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=code,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=retryable,
        conflict_with=conflict_with,
    )


def _build_checkpoint_tuple_mapper(
    settings: CheckpointStoreSettings,
) -> CheckpointTupleMapper:
    """根据 CheckpointStore RuntimeConfig 构建默认 checkpoint tuple 映射器。

    :param settings: CheckpointStore RuntimeConfig 快照。
    :return: 已接入 schema 兼容策略的 checkpoint tuple 映射器。
    """

    return CheckpointTupleMapper(
        supported_state_schema_versions=frozenset(
            settings.state_schema.supported_state_schema_versions
        )
    )


def _thread_row_to_dto(row: RowMapping) -> CheckpointThreadDto:
    """将 checkpoint_thread 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 checkpoint_thread 行。
    :return: 转换后的 checkpoint thread DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return CheckpointThreadDto.model_validate(dict(row))


def _build_thread_id_or_raise(command: EnsureThreadCommandDto) -> str:
    """根据 EnsureThread 命令构建 thread_id，并映射非法入参错误。

    :param command: 获取或创建 checkpoint thread 的命令 DTO。
    :return: 根据 session_id 派生出的稳定 thread_id。
    :raises CheckpointStoreError: 当 session_id 无法派生合法 thread_id 时抛出。
    """

    try:
        return build_checkpoint_thread_id(session_id=command.session_id)
    except ValueError as exc:
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.ENSURE_THREAD,
            message=str(exc),
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
        ) from exc


def _build_store_unavailable_error(
    *,
    command: EnsureThreadCommandDto,
    message: str,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 EnsureThread 存储不可用错误。

    :param command: 获取或创建 checkpoint thread 的命令 DTO。
    :param message: 面向工程排障的错误说明。
    :param conflict_with: 可选冲突对象摘要。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
        operation=CheckpointOperation.ENSURE_THREAD,
        message=message,
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=True,
        conflict_with=conflict_with,
    )


def _build_timeout_error(
    *,
    command: EnsureThreadCommandDto,
    message: str,
) -> CheckpointStoreError:
    """构建 EnsureThread 数据库超时错误。

    :param command: 获取或创建 checkpoint thread 的命令 DTO。
    :param message: 面向工程排障的错误说明。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
        operation=CheckpointOperation.ENSURE_THREAD,
        message=message,
        request_id=command.request_id,
        trace_id=command.trace_id,
        retryable=True,
    )


class SqlAlchemyCheckpointThreadRepository:
    """基于 SQLAlchemy Core 的 checkpoint_thread 仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 checkpoint_thread 仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def dispose(self) -> None:
        """释放仓储持有的数据库连接池资源。

        :return: None。
        """

        self._engine.dispose()

    def ensure_thread(
        self,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """获取或创建 checkpoint thread。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 当前请求命中的 checkpoint thread 与创建标记。
        :raises CheckpointStoreError: 当 pet_id 冲突、入参无效或存储不可用时抛出。
        """

        thread_id = _build_thread_id_or_raise(command)
        try:
            try:
                return self._ensure_thread_once(
                    command=command,
                    thread_id=thread_id,
                )
            except IntegrityError as exc:
                return self._ensure_thread_after_insert_conflict(
                    command=command,
                    thread_id=thread_id,
                    insert_error=exc,
                )
        except CheckpointStoreError:
            raise
        except ValidationError as exc:
            raise _build_store_unavailable_error(
                command=command,
                message="checkpoint_thread 行结构不符合 CheckpointThreadDto 契约",
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise _build_timeout_error(
                command=command,
                message="EnsureThread 数据库操作超时",
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_store_unavailable_error(
                command=command,
                message="EnsureThread 数据库操作失败",
            ) from exc

    def _ensure_thread_once(
        self,
        *,
        command: EnsureThreadCommandDto,
        thread_id: str,
    ) -> EnsureThreadResultDto:
        """执行一次 EnsureThread 事务。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :param thread_id: 已根据 session_id 派生的稳定 thread_id。
        :return: 当前请求命中的 checkpoint thread 与创建标记。
        :raises IntegrityError: 当并发创建命中唯一约束时抛出。
        :raises CheckpointStoreError: 当已有 thread 与本次请求存在身份锚点冲突时抛出。
        """

        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing_row = self._fetch_thread_by_session(
                connection=connection,
                session_id=command.session_id,
                for_update=True,
            )
            if existing_row is not None:
                return self._resolve_existing_thread(
                    connection=connection,
                    row=existing_row,
                    command=command,
                )

            connection.execute(
                CHECKPOINT_THREAD_TABLE.insert().values(
                    thread_id=thread_id,
                    session_id=command.session_id,
                    user_id=command.user_id,
                    pet_id=command.pet_id,
                    status=CheckpointThreadStatus.INITIALIZED.value,
                    latest_version=0,
                    latest_checkpoint_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            created_row = self._fetch_thread_by_session(
                connection=connection,
                session_id=command.session_id,
                for_update=False,
            )
            if created_row is None:
                raise _build_store_unavailable_error(
                    command=command,
                    message="新建 checkpoint_thread 后无法读取已创建行",
                    conflict_with={"thread_id": thread_id},
                )
            return EnsureThreadResultDto(
                thread=_thread_row_to_dto(created_row),
                created_new=True,
            )

    def _ensure_thread_after_insert_conflict(
        self,
        *,
        command: EnsureThreadCommandDto,
        thread_id: str,
        insert_error: IntegrityError,
    ) -> EnsureThreadResultDto:
        """处理并发插入导致的唯一约束冲突。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :param thread_id: 已根据 session_id 派生的稳定 thread_id。
        :param insert_error: 首次插入捕获到的完整性错误。
        :return: 冲突后重读到的既有 checkpoint thread。
        :raises CheckpointStoreError: 当冲突后无法找到对应 session 或存在身份锚点冲突时抛出。
        """

        with self._engine.begin() as connection:
            existing_row = self._fetch_thread_by_session(
                connection=connection,
                session_id=command.session_id,
                for_update=True,
            )
            if existing_row is None:
                raise _build_store_unavailable_error(
                    command=command,
                    message="checkpoint_thread 插入冲突后无法读取既有 session",
                    conflict_with={
                        "thread_id": thread_id,
                        "reason": "insert_conflict_without_existing_thread",
                    },
                ) from insert_error
            return self._resolve_existing_thread(
                connection=connection,
                row=existing_row,
                command=command,
            )

    def _fetch_thread_by_session(
        self,
        *,
        connection: Connection,
        session_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        """按 session_id 精确读取 checkpoint thread。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param session_id: 上游可信传入的会话 ID。
        :param for_update: 是否请求数据库对命中的行加行级锁。
        :return: 命中的 checkpoint_thread 行；不存在时返回 None。
        """

        statement = CHECKPOINT_THREAD_TABLE.select().where(
            CHECKPOINT_THREAD_TABLE.c.session_id == session_id
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one_or_none()

    def _resolve_existing_thread(
        self,
        *,
        connection: Connection,
        row: RowMapping,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """校验并返回既有 checkpoint thread。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param row: 已通过 session_id 命中的 checkpoint_thread 行。
        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 当前请求命中的 checkpoint thread 与创建标记。
        :raises CheckpointStoreError: 当既有 thread 与本次请求存在 user_id 或 pet_id 冲突时抛出。
        """

        thread = _thread_row_to_dto(row)
        self._ensure_user_anchor_matches(thread=thread, command=command)
        self._ensure_pet_anchor_matches(thread=thread, command=command)
        if thread.pet_id is None and command.pet_id is not None:
            thread = self._bind_thread_pet_id(
                connection=connection,
                command=command,
            )
            self._ensure_pet_anchor_matches(thread=thread, command=command)
        return EnsureThreadResultDto(
            thread=thread,
            created_new=False,
        )

    def _bind_thread_pet_id(
        self,
        *,
        connection: Connection,
        command: EnsureThreadCommandDto,
    ) -> CheckpointThreadDto:
        """将未锚定宠物的 thread 绑定到本次请求的 pet_id。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 更新后的 checkpoint thread DTO。
        :raises CheckpointStoreError: 当更新后无法读取 thread 时抛出。
        """

        connection.execute(
            CHECKPOINT_THREAD_TABLE.update()
            .where(CHECKPOINT_THREAD_TABLE.c.session_id == command.session_id)
            .where(CHECKPOINT_THREAD_TABLE.c.pet_id.is_(None))
            .values(
                pet_id=command.pet_id,
                updated_at=datetime.now(UTC),
            )
        )
        updated_row = self._fetch_thread_by_session(
            connection=connection,
            session_id=command.session_id,
            for_update=True,
        )
        if updated_row is None:
            raise _build_store_unavailable_error(
                command=command,
                message="绑定 pet_id 后无法读取 checkpoint_thread",
                conflict_with={"session_id": command.session_id},
            )
        return _thread_row_to_dto(updated_row)

    def _ensure_user_anchor_matches(
        self,
        *,
        thread: CheckpointThreadDto,
        command: EnsureThreadCommandDto,
    ) -> None:
        """校验既有 thread 的 user_id 锚点与本次请求一致。

        :param thread: 已读取的 checkpoint thread DTO。
        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当既有 user_id 与本次请求不一致时抛出。
        """

        if thread.user_id == command.user_id:
            return
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.ENSURE_THREAD,
            message="checkpoint thread 已锚定到不同 user_id",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "existing_user_id": thread.user_id,
                "requested_user_id": command.user_id,
            },
        )

    def _ensure_pet_anchor_matches(
        self,
        *,
        thread: CheckpointThreadDto,
        command: EnsureThreadCommandDto,
    ) -> None:
        """校验既有 thread 的 pet_id 锚点与本次请求不冲突。

        :param thread: 已读取的 checkpoint thread DTO。
        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当既有 pet_id 与本次请求 pet_id 冲突时抛出。
        """

        if thread.pet_id is None or command.pet_id is None:
            return
        if thread.pet_id == command.pet_id:
            return
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_PET_CONFLICT,
            operation=CheckpointOperation.ENSURE_THREAD,
            message="checkpoint thread 已锚定到不同 pet_id",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "existing_pet_id": thread.pet_id,
                "requested_pet_id": command.pet_id,
            },
        )


class SqlAlchemyCheckpointStore(TodoCheckpointStore):
    """基于 SQLAlchemy 控制面仓储的 CheckpointStore 实现。"""

    def __init__(
        self,
        *,
        thread_repository: SqlAlchemyCheckpointThreadRepository,
        run_lock_repository: SqlAlchemyCheckpointRunLockRepository,
        version_repository: SqlAlchemyCheckpointVersionRepository,
        read_repository: SqlAlchemyCheckpointReadRepository,
        segment_publish_repository: SqlAlchemyCheckpointSegmentPublishRepository,
        settings: CheckpointStoreSettings,
        checkpoint_mapper: CheckpointTupleMapper,
        checkpoint_reader: LangGraphCheckpointReader | None = None,
        checkpoint_writer: LangGraphCheckpointWriter | None = None,
    ) -> None:
        """初始化 SQLAlchemy CheckpointStore。

        :param thread_repository: checkpoint_thread 仓储实例。
        :param run_lock_repository: checkpoint_run_lock 仓储实例。
        :param version_repository: checkpoint_thread 版本推进仓储实例。
        :param read_repository: checkpoint 读取侧控制面仓储实例。
        :param segment_publish_repository: segment 发布幂等写入仓储实例。
        :param settings: CheckpointStore RuntimeConfig 快照。
        :param checkpoint_mapper: LangGraph checkpoint tuple 到项目 DTO 的映射器。
        :param checkpoint_reader: 可选 LangGraph checkpoint 读取适配器；未接入时读取 checkpoint 会显式失败。
        :param checkpoint_writer: 可选 LangGraph checkpoint 写入适配器；未接入时保存 checkpoint 会显式失败。
        :return: None。
        """

        self._thread_repository = thread_repository
        self._run_lock_repository = run_lock_repository
        self._version_repository = version_repository
        self._read_repository = read_repository
        self._segment_publish_repository = segment_publish_repository
        self._settings = settings
        self._checkpoint_mapper = checkpoint_mapper
        self._checkpoint_reader = checkpoint_reader
        self._checkpoint_writer = checkpoint_writer

    def dispose(self) -> None:
        """释放 CheckpointStore 持有的底层数据库资源。

        :return: None。
        """

        self._thread_repository.dispose()

    async def _with_operation_timeout(
        self,
        *,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
        awaitable: Awaitable[_T],
    ) -> _T:
        """按 RuntimeConfig 操作超时预算等待异步调用完成。

        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param awaitable: 需要受超时预算约束的异步调用。
        :return: 异步调用返回值。
        :raises CheckpointStoreError: 当等待超过配置的操作超时时间时抛出。
        """

        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=self._settings.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            raise _build_runtime_config_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="CheckpointStore 操作超过 RuntimeConfig 配置的超时预算",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={
                    "operation_timeout_seconds": self._settings.operation_timeout_seconds
                },
            ) from exc

    def _validate_run_lock_ttl(
        self,
        *,
        command: AcquireRunLockCommandDto,
    ) -> None:
        """校验运行锁 TTL 是否位于 RuntimeConfig 允许范围内。

        :param command: 获取运行锁的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 TTL 低于最小值或高于最大值时抛出。
        """

        min_ttl = self._settings.run_lock.min_ttl_seconds
        max_ttl = self._settings.run_lock.max_ttl_seconds
        if min_ttl <= command.lock_ttl_seconds <= max_ttl:
            return
        raise _build_runtime_config_error(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
            message="运行锁 TTL 超出 RuntimeConfig 允许范围",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "requested_ttl_seconds": command.lock_ttl_seconds,
                "min_ttl_seconds": min_ttl,
                "max_ttl_seconds": max_ttl,
            },
        )

    def _validate_list_limit(
        self,
        *,
        query: ListCheckpointsQueryDto,
    ) -> None:
        """校验 checkpoint 历史查询分页大小是否满足 RuntimeConfig。

        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: None。
        :raises CheckpointStoreError: 当分页大小超过 RuntimeConfig 上限时抛出。
        """

        max_list_limit = self._settings.history.max_list_limit
        if query.limit <= max_list_limit:
            return
        raise _build_runtime_config_error(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.LIST_CHECKPOINTS,
            message="ListCheckpoints limit 超出 RuntimeConfig 允许范围",
            request_id=query.request_id,
            trace_id=query.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": query.thread_id,
                "requested_limit": query.limit,
                "max_list_limit": max_list_limit,
            },
        )

    def _validate_segment_metadata_size(
        self,
        *,
        command: MarkSegmentPublishedCommandDto,
    ) -> None:
        """校验 segment 发布 metadata 序列化大小是否满足 RuntimeConfig。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 metadata 无法稳定序列化或超过 RuntimeConfig 上限时抛出。
        """

        try:
            metadata_size_bytes = _measure_json_bytes(command.metadata)
        except (TypeError, ValueError) as exc:
            raise _build_runtime_config_error(
                code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
                operation=CheckpointOperation.MARK_SEGMENT_PUBLISHED,
                message="segment 发布 metadata 不是合法 JSON 值",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={
                    "thread_id": command.thread_id,
                    "segment_id": command.segment_id,
                    "reason": str(exc),
                },
            ) from exc
        max_metadata_bytes = self._settings.segment_publish.max_metadata_bytes
        if metadata_size_bytes <= max_metadata_bytes:
            return
        raise _build_runtime_config_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE,
            operation=CheckpointOperation.MARK_SEGMENT_PUBLISHED,
            message="segment 发布 metadata 超出 RuntimeConfig 字节上限",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "segment_id": command.segment_id,
                "metadata_size_bytes": metadata_size_bytes,
                "max_metadata_bytes": max_metadata_bytes,
            },
        )

    def _validate_save_schema_version(
        self,
        *,
        command: SaveCheckpointCommandDto,
    ) -> None:
        """校验 SaveCheckpoint 写入的状态 schema 版本是否受 RuntimeConfig 支持。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 schema 版本不受当前 RuntimeConfig 支持时抛出。
        """

        supported_versions = set(
            self._settings.state_schema.supported_state_schema_versions
        )
        if command.state_schema_version in supported_versions:
            return
        raise _build_runtime_config_error(
            code=CheckpointErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED,
            operation=CheckpointOperation.SAVE_CHECKPOINT,
            message="SaveCheckpoint state_schema_version 不受 RuntimeConfig 支持",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "state_schema_version": command.state_schema_version,
                "supported_state_schema_versions": sorted(supported_versions),
            },
        )

    def _measure_save_state_size(
        self,
        *,
        command: SaveCheckpointCommandDto,
    ) -> int:
        """计算 SaveCheckpoint 状态体序列化字节数。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: checkpoint 状态体序列化后的 UTF-8 字节数。
        :raises CheckpointStoreError: 当状态体无法稳定 JSON 序列化时抛出。
        """

        state_payload = {
            "graph_state": command.graph_state.model_dump(mode="json"),
            "business_state": command.business_state.model_dump(mode="json"),
            "metadata": command.metadata,
        }
        try:
            state_size_bytes = _measure_json_bytes(state_payload)
        except (TypeError, ValueError) as exc:
            raise _build_runtime_config_error(
                code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                message="SaveCheckpoint 状态体不是合法 JSON 值",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={
                    "thread_id": command.thread_id,
                    "reason": str(exc),
                },
            ) from exc
        max_state_bytes = self._settings.checkpoint.max_state_bytes
        if state_size_bytes <= max_state_bytes:
            return state_size_bytes
        raise _build_runtime_config_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE,
            operation=CheckpointOperation.SAVE_CHECKPOINT,
            message="SaveCheckpoint 状态体超出 RuntimeConfig 字节上限",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "state_size_bytes": state_size_bytes,
                "max_state_bytes": max_state_bytes,
            },
        )

    def _validate_save_thread_state(
        self,
        *,
        command: SaveCheckpointCommandDto,
        thread: CheckpointThreadDto,
    ) -> None:
        """校验 SaveCheckpoint 命令与 checkpoint thread 锚点一致。

        :param command: 保存 checkpoint 的命令 DTO。
        :param thread: 已读取的 checkpoint thread DTO。
        :return: None。
        :raises CheckpointStoreError: 当 session、pet 或 expected_version 不一致时抛出。
        """

        if command.session_id != thread.session_id:
            raise _build_runtime_config_error(
                code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                message="SaveCheckpoint session_id 与 checkpoint thread 锚点不一致",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={
                    "thread_id": thread.thread_id,
                    "expected_session_id": thread.session_id,
                    "actual_session_id": command.session_id,
                },
            )
        if command.expected_version != thread.latest_version:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT,
                operation=CheckpointOperation.SAVE_CHECKPOINT,
                message="checkpoint thread latest_version 与 expected_version 不一致",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
                conflict_with={
                    "thread_id": thread.thread_id,
                    "expected_version": command.expected_version,
                    "actual_version": thread.latest_version,
                    "latest_checkpoint_id": thread.latest_checkpoint_id,
                },
            )
        self._validate_save_pet_anchor(command=command, thread=thread)

    def _validate_save_pet_anchor(
        self,
        *,
        command: SaveCheckpointCommandDto,
        thread: CheckpointThreadDto,
    ) -> None:
        """校验 SaveCheckpoint 业务状态中的宠物锚点不污染 thread。

        :param command: 保存 checkpoint 的命令 DTO。
        :param thread: 已读取的 checkpoint thread DTO。
        :return: None。
        :raises CheckpointStoreError: 当 business_state.pet_id 与 thread pet_id 冲突时抛出。
        """

        state_pet_id = command.business_state.pet_id
        if (
            thread.pet_id is None
            or state_pet_id is None
            or state_pet_id == thread.pet_id
        ):
            return
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_PET_CONFLICT,
            operation=CheckpointOperation.SAVE_CHECKPOINT,
            message="SaveCheckpoint business_state.pet_id 与 checkpoint thread pet_id 锚点不一致",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "thread_pet_id": thread.pet_id,
                "state_pet_id": state_pet_id,
            },
        )

    def _require_checkpoint_writer(
        self,
        *,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> LangGraphCheckpointWriter:
        """读取已接入的 LangGraph checkpoint writer。

        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 已接入的 LangGraph checkpoint writer。
        :raises CheckpointStoreError: 当 checkpoint writer 尚未接入时抛出。
        """

        if self._checkpoint_writer is not None:
            return self._checkpoint_writer
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
            operation=operation,
            message="LangGraph checkpoint writer 尚未接入",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )

    async def ensure_thread(
        self,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """获取或创建 checkpoint thread。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 当前请求命中的 checkpoint thread 与创建标记。
        :raises CheckpointStoreError: 当 pet_id 冲突、入参无效或存储不可用时抛出。
        """

        return await self._with_operation_timeout(
            operation=CheckpointOperation.ENSURE_THREAD,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(self._thread_repository.ensure_thread, command),
        )

    async def acquire_run_lock(
        self,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """获取同一 thread 的运行互斥锁。

        :param command: 获取运行锁的命令 DTO。
        :return: 运行锁获取结果。
        :raises CheckpointStoreError: 当锁被其他 run 持有、thread 不存在或存储不可用时抛出。
        """

        self._validate_run_lock_ttl(command=command)
        return await self._with_operation_timeout(
            operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._run_lock_repository.acquire_run_lock,
                command,
            ),
        )

    async def release_run_lock(
        self,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """释放当前 run 持有的运行锁。

        :param command: 释放运行锁的命令 DTO。
        :return: 运行锁释放结果。
        :raises CheckpointStoreError: 当释放者不是锁持有者或存储不可用时抛出。
        """

        return await self._with_operation_timeout(
            operation=CheckpointOperation.RELEASE_RUN_LOCK,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._run_lock_repository.release_run_lock,
                command,
            ),
        )

    async def load_latest_checkpoint(
        self,
        query: LoadLatestCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取指定 thread 的最新 checkpoint。

        :param query: 读取最新 checkpoint 的查询 DTO。
        :return: 最新 checkpoint、版本号与已发布 segment 摘要。
        :raises CheckpointStoreError: 当 thread 不存在、状态损坏、schema 不支持或存储不可用时抛出。
        """

        operation = CheckpointOperation.LOAD_LATEST_CHECKPOINT
        thread = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_thread,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        published_segments = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.list_published_segments,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        if thread.latest_version == 0 and thread.latest_checkpoint_id is None:
            return LoadLatestCheckpointResultDto(
                thread_id=query.thread_id,
                latest_version=0,
                checkpoint=None,
                published_segments=published_segments,
            )
        latest_checkpoint_id = thread.latest_checkpoint_id
        if latest_checkpoint_id is None:
            self._raise_corrupted_latest_pointer(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                thread=thread,
            )
            return LoadLatestCheckpointResultDto(
                thread_id=query.thread_id,
                latest_version=thread.latest_version,
                checkpoint=None,
                published_segments=published_segments,
            )
        checkpoint_reader = self._require_checkpoint_reader(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        checkpoint_tuple = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=checkpoint_reader.load_checkpoint_tuple(
                thread_id=query.thread_id,
                checkpoint_id=latest_checkpoint_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                missing_as_corrupted=True,
            ),
        )
        checkpoint = self._checkpoint_mapper.to_snapshot(
            checkpoint_tuple=checkpoint_tuple,
            expected_thread_id=query.thread_id,
            expected_checkpoint_id=latest_checkpoint_id,
            expected_version=thread.latest_version,
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        return LoadLatestCheckpointResultDto(
            thread_id=query.thread_id,
            latest_version=thread.latest_version,
            checkpoint=checkpoint,
            published_segments=published_segments,
        )

    async def get_checkpoint(
        self,
        query: GetCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取指定 checkpoint 快照。

        :param query: 读取指定 checkpoint 的查询 DTO。
        :return: 指定 checkpoint 快照与关联发布状态。
        :raises CheckpointStoreError: 当 checkpoint 不存在、跨 thread 读取或状态不可用时抛出。
        """

        operation = CheckpointOperation.GET_CHECKPOINT
        thread = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_thread,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        checkpoint_reader = self._require_checkpoint_reader(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        checkpoint_tuple = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=checkpoint_reader.load_checkpoint_tuple(
                thread_id=query.thread_id,
                checkpoint_id=query.checkpoint_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                missing_as_corrupted=False,
            ),
        )
        checkpoint = self._checkpoint_mapper.to_snapshot(
            checkpoint_tuple=checkpoint_tuple,
            expected_thread_id=query.thread_id,
            expected_checkpoint_id=query.checkpoint_id,
            expected_version=None,
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        published_segments = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.list_published_segments,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        return LoadLatestCheckpointResultDto(
            thread_id=query.thread_id,
            latest_version=thread.latest_version,
            checkpoint=checkpoint,
            published_segments=published_segments,
        )

    async def list_checkpoints(
        self,
        query: ListCheckpointsQueryDto,
    ) -> ListCheckpointsResultDto:
        """查询指定 thread 的 checkpoint 历史摘要。

        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: checkpoint 历史摘要分页结果。
        :raises CheckpointStoreError: 当 thread 不存在或存储不可用时抛出。
        """

        operation = CheckpointOperation.LIST_CHECKPOINTS
        self._validate_list_limit(query=query)
        await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_thread,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        checkpoint_reader = self._require_checkpoint_reader(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        metadata_filter = self._build_list_metadata_filter(query=query)
        checkpoint_tuples = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=checkpoint_reader.list_checkpoint_tuples(
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                limit=query.limit + 1,
                cursor=query.cursor,
                metadata_filter=metadata_filter,
            ),
        )
        summaries = [
            self._checkpoint_mapper.to_summary(
                checkpoint_tuple=checkpoint_tuple,
                expected_thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            )
            for checkpoint_tuple in checkpoint_tuples
        ]
        filtered_summaries = [
            summary
            for summary in summaries
            if self._summary_matches_time_filter(summary=summary, query=query)
        ]
        page_items = filtered_summaries[: query.limit]
        next_cursor = (
            summaries[query.limit].checkpoint_id
            if len(summaries) > query.limit
            else None
        )
        return ListCheckpointsResultDto(
            thread_id=query.thread_id,
            items=page_items,
            next_cursor=next_cursor,
        )

    async def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """幂等标记 segment 已发布。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: segment 发布状态与幂等命中标记。
        :raises CheckpointStoreError: 当 thread 不存在、行结构异常或存储不可用时抛出。
        """

        self._validate_segment_metadata_size(command=command)
        return await self._with_operation_timeout(
            operation=CheckpointOperation.MARK_SEGMENT_PUBLISHED,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._segment_publish_repository.mark_segment_published,
                command,
            ),
        )

    async def save_checkpoint(
        self,
        command: SaveCheckpointCommandDto,
    ) -> SaveCheckpointResultDto:
        """保存一次关键边界 checkpoint。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: 保存成功后的 checkpoint ID、版本和状态体大小。
        :raises CheckpointStoreError: 当未持锁、版本冲突、状态损坏、schema 不支持或存储不可用时抛出。
        """

        operation = CheckpointOperation.SAVE_CHECKPOINT
        self._validate_save_schema_version(command=command)
        state_size_bytes = self._measure_save_state_size(command=command)
        thread = await self._with_operation_timeout(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_thread,
                thread_id=command.thread_id,
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
            ),
        )
        self._validate_save_thread_state(command=command, thread=thread)
        await self._with_operation_timeout(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._run_lock_repository.ensure_run_lock_held,
                command,
            ),
        )
        checkpoint_writer = self._require_checkpoint_writer(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        checkpoint_id = await self._with_operation_timeout(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=checkpoint_writer.save_checkpoint(
                command=command,
                state_size_bytes=state_size_bytes,
            ),
        )
        return await self._with_operation_timeout(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._version_repository.advance_thread_version,
                command=command,
                checkpoint_id=checkpoint_id,
                state_size_bytes=state_size_bytes,
            ),
        )

    async def load_session_state(
        self,
        query: LoadSessionStateQueryDto,
    ) -> LoadSessionStateResultDto:
        """读取 session 短期业务状态摘要。

        :param query: 读取 session 状态摘要的查询 DTO。
        :return: session 短期业务状态摘要。
        :raises CheckpointStoreError: 当 thread 不存在、session 不一致、状态损坏或存储不可用时抛出。
        """

        operation = CheckpointOperation.LOAD_SESSION_STATE
        thread = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_thread,
                thread_id=query.thread_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ),
        )
        self._ensure_session_query_matches_thread(query=query, thread=thread)
        if thread.latest_version == 0 and thread.latest_checkpoint_id is None:
            return LoadSessionStateResultDto(
                thread_id=thread.thread_id,
                session_id=thread.session_id,
                latest_checkpoint_id=None,
                latest_version=0,
                state=SessionBusinessStateDto(pet_id=thread.pet_id),
            )
        latest_checkpoint_id = thread.latest_checkpoint_id
        if latest_checkpoint_id is None:
            self._raise_corrupted_latest_pointer(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                thread=thread,
            )
            return LoadSessionStateResultDto(
                thread_id=thread.thread_id,
                session_id=thread.session_id,
                latest_checkpoint_id=None,
                latest_version=thread.latest_version,
                state=SessionBusinessStateDto(pet_id=thread.pet_id),
            )
        checkpoint_reader = self._require_checkpoint_reader(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        checkpoint_tuple = await self._with_operation_timeout(
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=checkpoint_reader.load_checkpoint_tuple(
                thread_id=query.thread_id,
                checkpoint_id=latest_checkpoint_id,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                missing_as_corrupted=True,
            ),
        )
        checkpoint = self._checkpoint_mapper.to_snapshot(
            checkpoint_tuple=checkpoint_tuple,
            expected_thread_id=query.thread_id,
            expected_checkpoint_id=latest_checkpoint_id,
            expected_version=thread.latest_version,
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        state = self._resolve_session_business_state(
            thread=thread,
            state=checkpoint.business_state,
            operation=operation,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )
        return LoadSessionStateResultDto(
            thread_id=thread.thread_id,
            session_id=thread.session_id,
            latest_checkpoint_id=latest_checkpoint_id,
            latest_version=thread.latest_version,
            state=state,
        )

    def _require_checkpoint_reader(
        self,
        *,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> LangGraphCheckpointReader:
        """读取已接入的 LangGraph checkpoint reader。

        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 已接入的 LangGraph checkpoint reader。
        :raises CheckpointStoreError: 当 checkpoint reader 尚未接入时抛出。
        """

        if self._checkpoint_reader is not None:
            return self._checkpoint_reader
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
            operation=operation,
            message="LangGraph checkpoint reader 尚未接入",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )

    def _raise_corrupted_latest_pointer(
        self,
        *,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
        thread: CheckpointThreadDto,
    ) -> NoReturn:
        """抛出最新 checkpoint 指针损坏错误。

        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param thread: 已读取的 checkpoint thread DTO。
        :return: None。
        :raises CheckpointStoreError: 始终抛出 CHECKPOINT_STATE_CORRUPTED。
        """

        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="checkpoint thread latest_version 非 0 但 latest_checkpoint_id 为空",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "latest_version": thread.latest_version,
            },
        )

    def _ensure_session_query_matches_thread(
        self,
        *,
        query: LoadSessionStateQueryDto,
        thread: CheckpointThreadDto,
    ) -> None:
        """校验 LoadSessionState 查询携带的 session_id 与 thread 锚点一致。

        :param query: 读取 session 状态摘要的查询 DTO。
        :param thread: 已读取的 checkpoint thread DTO。
        :return: None。
        :raises CheckpointStoreError: 当查询 session_id 与 thread 锚点不一致时抛出。
        """

        if query.session_id is None or query.session_id == thread.session_id:
            return
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.LOAD_SESSION_STATE,
            message="LoadSessionState 查询 session_id 与 checkpoint thread 锚点不一致",
            request_id=query.request_id,
            trace_id=query.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "expected_session_id": query.session_id,
                "actual_session_id": thread.session_id,
            },
        )

    def _resolve_session_business_state(
        self,
        *,
        thread: CheckpointThreadDto,
        state: SessionBusinessStateDto,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> SessionBusinessStateDto:
        """根据 thread pet 锚点校验并补齐 session 业务状态。

        :param thread: 已读取的 checkpoint thread DTO。
        :param state: checkpoint 中保存的 session 短期业务状态。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 校验并补齐后的 session 短期业务状态。
        :raises CheckpointStoreError: 当 business_state.pet_id 与 thread.pet_id 冲突时抛出。
        """

        if thread.pet_id is None:
            return state
        if state.pet_id is None:
            return state.model_copy(update={"pet_id": thread.pet_id})
        if state.pet_id == thread.pet_id:
            return state
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="checkpoint business_state.pet_id 与 thread pet_id 锚点不一致",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread.thread_id,
                "thread_pet_id": thread.pet_id,
                "state_pet_id": state.pet_id,
            },
        )

    def _build_list_metadata_filter(
        self,
        *,
        query: ListCheckpointsQueryDto,
    ) -> dict[str, object] | None:
        """构建 LangGraph checkpoint 历史查询 metadata 过滤条件。

        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: 可传递给 LangGraph alist 的 metadata filter；无过滤条件时返回 None。
        """

        if query.status is None:
            return None
        return {"checkpoint_store": {"status": query.status.value}}

    def _summary_matches_time_filter(
        self,
        *,
        summary: CheckpointSummaryDto,
        query: ListCheckpointsQueryDto,
    ) -> bool:
        """判断 checkpoint 摘要是否满足时间过滤条件。

        :param summary: checkpoint 历史摘要 DTO。
        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: 若摘要满足时间过滤条件则返回 True。
        """

        if query.created_after is not None and summary.created_at < query.created_after:
            return False
        if (
            query.created_before is not None
            and summary.created_at > query.created_before
        ):
            return False
        return True


def create_sqlalchemy_checkpoint_store(
    database_url: str,
    *,
    settings: CheckpointStoreSettings | None = None,
    checkpoint_reader: LangGraphCheckpointReader | None = None,
    checkpoint_writer: LangGraphCheckpointWriter | None = None,
    checkpoint_mapper: CheckpointTupleMapper | None = None,
) -> SqlAlchemyCheckpointStore:
    """创建基于 SQLAlchemy 的 CheckpointStore 实例。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param settings: 可选 CheckpointStore RuntimeConfig；未传入时从默认配置源加载。
    :param checkpoint_reader: 可选 LangGraph checkpoint 读取适配器。
    :param checkpoint_writer: 可选 LangGraph checkpoint 写入适配器。
    :param checkpoint_mapper: 可选 LangGraph checkpoint tuple 映射器。
    :return: 已装配控制面仓储与 RuntimeConfig 的 CheckpointStore 实例。
    """

    resolved_settings = (
        load_checkpoint_store_settings() if settings is None else settings
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    thread_repository = SqlAlchemyCheckpointThreadRepository(engine=engine)
    run_lock_repository = SqlAlchemyCheckpointRunLockRepository(engine=engine)
    version_repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    read_repository = SqlAlchemyCheckpointReadRepository(engine=engine)
    segment_publish_repository = SqlAlchemyCheckpointSegmentPublishRepository(
        engine=engine
    )
    return SqlAlchemyCheckpointStore(
        thread_repository=thread_repository,
        run_lock_repository=run_lock_repository,
        version_repository=version_repository,
        read_repository=read_repository,
        segment_publish_repository=segment_publish_repository,
        settings=resolved_settings,
        checkpoint_reader=checkpoint_reader,
        checkpoint_writer=checkpoint_writer,
        checkpoint_mapper=_build_checkpoint_tuple_mapper(resolved_settings)
        if checkpoint_mapper is None
        else checkpoint_mapper,
    )


__all__: tuple[str, ...] = (
    "CHECKPOINT_RUN_LOCK_TABLE",
    "CHECKPOINT_SEGMENT_PUBLISH_TABLE",
    "CHECKPOINT_STORE_METADATA",
    "CHECKPOINT_THREAD_TABLE",
    "SqlAlchemyCheckpointStore",
    "SqlAlchemyCheckpointReadRepository",
    "SqlAlchemyCheckpointRunLockRepository",
    "SqlAlchemyCheckpointSegmentPublishRepository",
    "SqlAlchemyCheckpointThreadRepository",
    "create_sqlalchemy_checkpoint_store",
)
