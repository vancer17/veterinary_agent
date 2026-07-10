##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_session.py
# 作用: 提供基于 SQLAlchemy Core 的 ConversationStore session 仓储，覆盖创建、读取、关闭与归档。
# 边界: 仅访问 ConversationStore 自有 session 表，不执行宠物授权、业务策略判断或 Agent 编排。
##################################################################################################

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import Connection
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from veterinary_agent.conversation_store.dto import (
    ArchiveSessionCommandDto,
    CloseSessionCommandDto,
    ConversationSessionDto,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    GetSessionQueryDto,
    UpdateSessionStatusResultDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationOperation,
    ConversationSessionStatus,
)
from veterinary_agent.conversation_store.errors import ConversationStoreError
from veterinary_agent.conversation_store.sqlalchemy_common import (
    build_conversation_error,
    merge_metadata,
    raise_not_found,
    raise_session_anchor_conflict,
    row_to_session_dto,
)
from veterinary_agent.conversation_store.sqlalchemy_tables import (
    CONVERSATION_SESSION_TABLE,
)


class SqlAlchemyConversationSessionRepository:
    """基于 SQLAlchemy Core 的 conversation_session 仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 conversation_session 仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def dispose(self) -> None:
        """释放仓储持有的数据库连接池资源。

        :return: None。
        """

        self._engine.dispose()

    def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认 conversation session。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前请求命中的 session 与创建标记。
        :raises ConversationStoreError: 当锚点冲突、数据库不可用或行结构非法时抛出。
        """

        try:
            try:
                return self._ensure_session_once(command=command)
            except IntegrityError as exc:
                return self._ensure_session_after_insert_conflict(
                    command=command,
                    insert_error=exc,
                )
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=ConversationOperation.ENSURE_SESSION,
                message="conversation_session 行结构不符合 ConversationSessionDto 契约",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.OPERATION_TIMEOUT,
                operation=ConversationOperation.ENSURE_SESSION,
                message="EnsureSession 数据库操作超时",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=ConversationOperation.ENSURE_SESSION,
                message="EnsureSession 数据库操作失败",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
            ) from exc

    def get_session(
        self,
        query: GetSessionQueryDto,
    ) -> ConversationSessionDto:
        """读取 conversation session。

        :param query: 读取 conversation session 的查询 DTO。
        :return: 命中的 conversation session。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突、数据库不可用或行结构非法时抛出。
        """

        operation = ConversationOperation.GET_SESSION
        try:
            with self._engine.begin() as connection:
                row = self._fetch_session(
                    connection=connection,
                    session_id=query.session_id,
                    for_update=False,
                )
                if row is None:
                    raise_not_found(
                        operation=operation,
                        request_id=query.request_id,
                        trace_id=query.trace_id,
                        session_id=query.session_id,
                    )
                session = row_to_session_dto(row)
                raise_session_anchor_conflict(
                    operation=operation,
                    request_id=query.request_id,
                    trace_id=query.trace_id,
                    session=session,
                    requested_user_id=query.user_id,
                    requested_pet_id=query.pet_id,
                )
                return session
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=operation,
                message="conversation_session 行结构不符合 ConversationSessionDto 契约",
                request_id=query.request_id,
                trace_id=query.trace_id,
                retryable=True,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.OPERATION_TIMEOUT,
                operation=operation,
                message="GetSession 数据库操作超时",
                request_id=query.request_id,
                trace_id=query.trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=operation,
                message="GetSession 数据库操作失败",
                request_id=query.request_id,
                trace_id=query.trace_id,
                retryable=True,
            ) from exc

    def close_session(
        self,
        command: CloseSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """关闭 conversation session。

        :param command: 关闭 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或数据库不可用时抛出。
        """

        return self._update_session_status(
            operation=ConversationOperation.CLOSE_SESSION,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
            target_status=ConversationSessionStatus.CLOSED,
            metadata_patch=command.metadata_patch,
        )

    def archive_session(
        self,
        command: ArchiveSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """归档 conversation session。

        :param command: 归档 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或数据库不可用时抛出。
        """

        return self._update_session_status(
            operation=ConversationOperation.ARCHIVE_SESSION,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
            target_status=ConversationSessionStatus.ARCHIVED,
            metadata_patch=command.metadata_patch,
        )

    def _ensure_session_once(
        self,
        *,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """执行一次 EnsureSession 事务。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前请求命中的 session 与创建标记。
        :raises IntegrityError: 当并发创建命中唯一约束时抛出。
        :raises ConversationStoreError: 当已有 session 与本次请求存在锚点冲突时抛出。
        """

        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            existing_row = self._fetch_session(
                connection=connection,
                session_id=command.session_id,
                for_update=True,
            )
            if existing_row is not None:
                return self._resolve_existing_session(
                    row=existing_row,
                    command=command,
                )

            connection.execute(
                CONVERSATION_SESSION_TABLE.insert().values(
                    session_id=command.session_id,
                    user_id=command.user_id,
                    pet_id=command.pet_id,
                    status=ConversationSessionStatus.ACTIVE.value,
                    metadata=command.metadata,
                    next_sequence_no=1,
                    created_at=now,
                    updated_at=now,
                    last_message_at=None,
                )
            )
            created_row = self._fetch_session(
                connection=connection,
                session_id=command.session_id,
                for_update=False,
            )
            if created_row is None:
                raise build_conversation_error(
                    code=ConversationErrorCode.STORE_UNAVAILABLE,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="新建 conversation_session 后无法读取已创建行",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=True,
                    conflict_with={"session_id": command.session_id},
                )
            return EnsureSessionResultDto(
                session=row_to_session_dto(created_row),
                created_new=True,
            )

    def _ensure_session_after_insert_conflict(
        self,
        *,
        command: EnsureSessionCommandDto,
        insert_error: IntegrityError,
    ) -> EnsureSessionResultDto:
        """处理并发插入导致的唯一约束冲突。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :param insert_error: 首次插入捕获到的完整性错误。
        :return: 冲突后重读到的既有 conversation session。
        :raises ConversationStoreError: 当冲突后无法找到对应 session 或存在锚点冲突时抛出。
        """

        with self._engine.begin() as connection:
            existing_row = self._fetch_session(
                connection=connection,
                session_id=command.session_id,
                for_update=True,
            )
            if existing_row is None:
                raise build_conversation_error(
                    code=ConversationErrorCode.STORE_UNAVAILABLE,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="conversation_session 插入冲突后无法读取既有 session",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=True,
                    conflict_with={
                        "session_id": command.session_id,
                        "reason": "insert_conflict_without_existing_session",
                    },
                ) from insert_error
            return self._resolve_existing_session(
                row=existing_row,
                command=command,
            )

    def _resolve_existing_session(
        self,
        *,
        row: RowMapping,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """校验并返回既有 conversation session。

        :param row: 已通过 session_id 命中的 conversation_session 行。
        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前请求命中的 session 与创建标记。
        :raises ConversationStoreError: 当既有 session 与本次请求存在 user_id 或 pet_id 冲突时抛出。
        """

        session = row_to_session_dto(row)
        raise_session_anchor_conflict(
            operation=ConversationOperation.ENSURE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            session=session,
            requested_user_id=command.user_id,
            requested_pet_id=command.pet_id,
        )
        return EnsureSessionResultDto(
            session=session,
            created_new=False,
        )

    def _fetch_session(
        self,
        *,
        connection: Connection,
        session_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        """按 session_id 精确读取 conversation session。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param session_id: 需要读取的 session ID。
        :param for_update: 是否请求数据库对命中的行加行级锁。
        :return: 命中的 conversation_session 行；不存在时返回 None。
        """

        statement = CONVERSATION_SESSION_TABLE.select().where(
            CONVERSATION_SESSION_TABLE.c.session_id == session_id
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one_or_none()

    def _update_session_status(
        self,
        *,
        operation: ConversationOperation,
        session_id: str,
        user_id: str,
        pet_id: str,
        request_id: str,
        trace_id: str,
        target_status: ConversationSessionStatus,
        metadata_patch: dict[str, object],
    ) -> UpdateSessionStatusResultDto:
        """更新 conversation session 状态。

        :param operation: 当前 ConversationStore 操作名。
        :param session_id: 需要更新的 session ID。
        :param user_id: 请求方用户 ID。
        :param pet_id: 请求方宠物 ID。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param target_status: 目标 session 状态。
        :param metadata_patch: 需要追加或覆盖的 session metadata。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或数据库不可用时抛出。
        """

        try:
            with self._engine.begin() as connection:
                row = self._fetch_session(
                    connection=connection,
                    session_id=session_id,
                    for_update=True,
                )
                if row is None:
                    raise_not_found(
                        operation=operation,
                        request_id=request_id,
                        trace_id=trace_id,
                        session_id=session_id,
                    )
                session = row_to_session_dto(row)
                raise_session_anchor_conflict(
                    operation=operation,
                    request_id=request_id,
                    trace_id=trace_id,
                    session=session,
                    requested_user_id=user_id,
                    requested_pet_id=pet_id,
                )
                idempotent = session.status is target_status
                if idempotent and not metadata_patch:
                    return UpdateSessionStatusResultDto(
                        session=session,
                        idempotent=True,
                    )

                now = datetime.now(UTC)
                metadata = merge_metadata(
                    original=session.metadata,
                    patch=metadata_patch,
                )
                connection.execute(
                    CONVERSATION_SESSION_TABLE.update()
                    .where(CONVERSATION_SESSION_TABLE.c.session_id == session_id)
                    .values(
                        status=target_status.value,
                        metadata=metadata,
                        updated_at=now,
                    )
                )
                updated_row = self._fetch_session(
                    connection=connection,
                    session_id=session_id,
                    for_update=False,
                )
                if updated_row is None:
                    raise_not_found(
                        operation=operation,
                        request_id=request_id,
                        trace_id=trace_id,
                        session_id=session_id,
                    )
                return UpdateSessionStatusResultDto(
                    session=row_to_session_dto(updated_row),
                    idempotent=idempotent,
                )
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=operation,
                message="conversation_session 行结构不符合 ConversationSessionDto 契约",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.OPERATION_TIMEOUT,
                operation=operation,
                message="session 状态更新数据库操作超时",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.STORE_UNAVAILABLE,
                operation=operation,
                message="session 状态更新数据库操作失败",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc


__all__: tuple[str, ...] = (
    "SqlAlchemyConversationSessionRepository",
)
