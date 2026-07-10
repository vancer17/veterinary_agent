##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_read.py
# 作用: 提供基于 SQLAlchemy Core 的 ConversationStore 读取侧仓储，覆盖 session 消息分页与最近消息读取。
# 边界: 仅访问 ConversationStore 自有表，不构建 prompt、不读取 checkpoint、不拼接 RAG、记忆或逻辑链。
##################################################################################################

from pydantic import ValidationError
from sqlalchemy import Select
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import (
    SQLAlchemyError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from veterinary_agent.conversation_store.dto import (
    ConversationMessageDto,
    GetRecentMessagesQueryDto,
    GetRecentMessagesResultDto,
    ListMessagesBySessionQueryDto,
    ListMessagesBySessionResultDto,
    MessageAttachmentRefDto,
    MessageSegmentDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationOperation,
)
from veterinary_agent.conversation_store.errors import ConversationStoreError
from veterinary_agent.conversation_store.sqlalchemy_common import (
    build_conversation_error,
    raise_not_found,
    raise_session_anchor_conflict,
    row_to_attachment_ref_dto,
    row_to_message_dto,
    row_to_segment_dto,
    row_to_session_dto,
)
from veterinary_agent.conversation_store.sqlalchemy_tables import (
    CONVERSATION_ATTACHMENT_REF_TABLE,
    CONVERSATION_MESSAGE_SEGMENT_TABLE,
    CONVERSATION_MESSAGE_TABLE,
    CONVERSATION_SESSION_TABLE,
)


class SqlAlchemyConversationReadRepository:
    """基于 SQLAlchemy Core 的 ConversationStore 读取侧仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 ConversationStore 读取侧仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def list_messages_by_session(
        self,
        query: ListMessagesBySessionQueryDto,
    ) -> ListMessagesBySessionResultDto:
        """按 session 分页查询消息。

        :param query: 按 session 分页查询消息的查询 DTO。
        :return: 按 sequence_no 升序返回的消息分页。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突、游标非法或数据库不可用时抛出。
        """

        operation = ConversationOperation.LIST_MESSAGES_BY_SESSION
        try:
            cursor_sequence_no = self._parse_cursor(
                cursor=query.cursor,
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            )
            with self._engine.begin() as connection:
                session_row = (
                    connection.execute(
                        CONVERSATION_SESSION_TABLE.select().where(
                            CONVERSATION_SESSION_TABLE.c.session_id == query.session_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if session_row is None:
                    raise_not_found(
                        operation=operation,
                        request_id=query.request_id,
                        trace_id=query.trace_id,
                        session_id=query.session_id,
                    )
                session = row_to_session_dto(session_row)
                raise_session_anchor_conflict(
                    operation=operation,
                    request_id=query.request_id,
                    trace_id=query.trace_id,
                    session=session,
                    requested_user_id=query.user_id,
                    requested_pet_id=query.pet_id,
                )
                statement = self._build_list_statement(
                    session_id=query.session_id,
                    cursor_sequence_no=cursor_sequence_no,
                    limit=query.limit + 1,
                )
                rows = connection.execute(statement).mappings().all()
                page_rows = rows[: query.limit]
                items = [
                    self._load_message_dto(
                        connection=connection,
                        row=row,
                        include_segments=query.include_segments,
                        include_attachments=query.include_attachments,
                    )
                    for row in page_rows
                ]
                next_cursor = (
                    str(page_rows[-1]["sequence_no"])
                    if len(rows) > query.limit and page_rows
                    else None
                )
                return ListMessagesBySessionResultDto(
                    session_id=query.session_id,
                    items=items,
                    next_cursor=next_cursor,
                )
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                message="ListMessagesBySession 数据库操作失败",
            ) from exc

    def get_recent_messages(
        self,
        query: GetRecentMessagesQueryDto,
    ) -> GetRecentMessagesResultDto:
        """读取最近消息。

        :param query: 读取最近消息的查询 DTO。
        :return: 按 sequence_no 升序返回的最近消息列表。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或数据库不可用时抛出。
        """

        operation = ConversationOperation.GET_RECENT_MESSAGES
        try:
            with self._engine.begin() as connection:
                session_row = (
                    connection.execute(
                        CONVERSATION_SESSION_TABLE.select().where(
                            CONVERSATION_SESSION_TABLE.c.session_id == query.session_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if session_row is None:
                    raise_not_found(
                        operation=operation,
                        request_id=query.request_id,
                        trace_id=query.trace_id,
                        session_id=query.session_id,
                    )
                session = row_to_session_dto(session_row)
                raise_session_anchor_conflict(
                    operation=operation,
                    request_id=query.request_id,
                    trace_id=query.trace_id,
                    session=session,
                    requested_user_id=query.user_id,
                    requested_pet_id=query.pet_id,
                )
                recent_rows = (
                    connection.execute(
                        CONVERSATION_MESSAGE_TABLE.select()
                        .where(
                            CONVERSATION_MESSAGE_TABLE.c.session_id == query.session_id
                        )
                        .order_by(CONVERSATION_MESSAGE_TABLE.c.sequence_no.desc())
                        .limit(query.limit)
                    )
                    .mappings()
                    .all()
                )
                ascending_rows = sorted(
                    recent_rows,
                    key=lambda row: int(row["sequence_no"]),
                )
                items = [
                    self._load_message_dto(
                        connection=connection,
                        row=row,
                        include_segments=query.include_segments,
                        include_attachments=query.include_attachments,
                    )
                    for row in ascending_rows
                ]
                return GetRecentMessagesResultDto(
                    session_id=query.session_id,
                    items=items,
                )
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=query.request_id,
                trace_id=query.trace_id,
                message="GetRecentMessages 数据库操作失败",
            ) from exc

    def _parse_cursor(
        self,
        *,
        cursor: str | None,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
    ) -> int | None:
        """解析分页游标。

        :param cursor: 分页游标；当前实现使用 sequence_no 字符串。
        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 游标对应的 sequence_no；无游标时返回 None。
        :raises ConversationStoreError: 当游标格式非法时抛出。
        """

        if cursor is None:
            return None
        try:
            sequence_no = int(cursor)
        except ValueError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.INVALID_ARGUMENT,
                operation=operation,
                message="消息分页 cursor 必须是 sequence_no 字符串",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"cursor": cursor},
            ) from exc
        if sequence_no < 0:
            raise build_conversation_error(
                code=ConversationErrorCode.INVALID_ARGUMENT,
                operation=operation,
                message="消息分页 cursor 不得为负数",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"cursor": cursor},
            )
        return sequence_no

    def _build_list_statement(
        self,
        *,
        session_id: str,
        cursor_sequence_no: int | None,
        limit: int,
    ) -> Select[tuple[object, ...]]:
        """构建消息分页查询语句。

        :param session_id: 需要查询的 session ID。
        :param cursor_sequence_no: 可选游标 sequence_no。
        :param limit: 实际查询条数。
        :return: SQLAlchemy Select 查询语句。
        """

        statement = CONVERSATION_MESSAGE_TABLE.select().where(
            CONVERSATION_MESSAGE_TABLE.c.session_id == session_id
        )
        if cursor_sequence_no is not None:
            statement = statement.where(
                CONVERSATION_MESSAGE_TABLE.c.sequence_no > cursor_sequence_no
            )
        return statement.order_by(CONVERSATION_MESSAGE_TABLE.c.sequence_no.asc()).limit(
            limit
        )

    def _load_message_dto(
        self,
        *,
        connection: Connection,
        row: RowMapping,
        include_segments: bool,
        include_attachments: bool,
    ) -> ConversationMessageDto:
        """读取 message 行关联对象并转换为 DTO。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param row: conversation_message 行。
        :param include_segments: 是否加载助手消息分段。
        :param include_attachments: 是否加载附件引用。
        :return: 转换后的 conversation message DTO。
        """

        message_id = str(row["message_id"])
        segments = (
            self._list_segments(connection=connection, message_id=message_id)
            if include_segments
            else []
        )
        attachments = (
            self._list_attachments(connection=connection, message_id=message_id)
            if include_attachments
            else []
        )
        return row_to_message_dto(
            row,
            segments=segments,
            attachments=attachments,
        )

    def _list_segments(
        self,
        *,
        connection: Connection,
        message_id: str,
    ) -> list[MessageSegmentDto]:
        """列出指定助手消息的分段。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 需要读取分段的消息 ID。
        :return: 按 segment_order 升序返回的分段 DTO 列表。
        """

        rows = (
            connection.execute(
                CONVERSATION_MESSAGE_SEGMENT_TABLE.select()
                .where(CONVERSATION_MESSAGE_SEGMENT_TABLE.c.message_id == message_id)
                .order_by(CONVERSATION_MESSAGE_SEGMENT_TABLE.c.segment_order.asc())
            )
            .mappings()
            .all()
        )
        return [row_to_segment_dto(row) for row in rows]

    def _list_attachments(
        self,
        *,
        connection: Connection,
        message_id: str,
    ) -> list[MessageAttachmentRefDto]:
        """列出指定消息的附件引用。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 需要读取附件引用的消息 ID。
        :return: 按创建时间升序返回的附件引用 DTO 列表。
        """

        rows = (
            connection.execute(
                CONVERSATION_ATTACHMENT_REF_TABLE.select()
                .where(CONVERSATION_ATTACHMENT_REF_TABLE.c.message_id == message_id)
                .order_by(CONVERSATION_ATTACHMENT_REF_TABLE.c.created_at.asc())
            )
            .mappings()
            .all()
        )
        return [row_to_attachment_ref_dto(row) for row in rows]

    def _build_validation_error(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        validation_error: ValidationError,
    ) -> ConversationStoreError:
        """构建 DTO 校验失败映射错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param validation_error: Pydantic 校验错误。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.STORE_UNAVAILABLE,
            operation=operation,
            message="ConversationStore 数据库行结构不符合 DTO 契约",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
            conflict_with={"validation_error_count": len(validation_error.errors())},
        )

    def _build_timeout_error(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
    ) -> ConversationStoreError:
        """构建数据库超时错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.OPERATION_TIMEOUT,
            operation=operation,
            message="ConversationStore 数据库操作超时",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )

    def _build_store_error(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message: str,
    ) -> ConversationStoreError:
        """构建数据库不可用错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message: 面向工程排障的错误说明。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.STORE_UNAVAILABLE,
            operation=operation,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )


__all__: tuple[str, ...] = ("SqlAlchemyConversationReadRepository",)
