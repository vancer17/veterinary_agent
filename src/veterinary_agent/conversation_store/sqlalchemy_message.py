##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_message.py
# 作用: 提供基于 SQLAlchemy Core 的 ConversationStore message/segment 写入仓储，覆盖消息追加、
#       助手消息容器、分段发布、完成消息和附件引用写入。
# 边界: 仅访问 ConversationStore 自有表，不执行宠物授权、RAG、模型调用、输出安全审查或业务编排。
##################################################################################################

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Connection
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from veterinary_agent.conversation_store.dto import (
    AppendAssistantSegmentCommandDto,
    AppendAssistantSegmentResultDto,
    AppendMessageCommandDto,
    AppendMessageResultDto,
    ConversationMessageDto,
    ConversationSessionDto,
    CreateAssistantMessageCommandDto,
    CreateAssistantMessageResultDto,
    FinalizeAssistantMessageCommandDto,
    FinalizeAssistantMessageResultDto,
    MessageAttachmentRefDto,
    MessageAttachmentRefInputDto,
    MessageSegmentDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationMessageRole,
    ConversationMessageStatus,
    ConversationOperation,
    ConversationSessionStatus,
)
from veterinary_agent.conversation_store.errors import ConversationStoreError
from veterinary_agent.conversation_store.sqlalchemy_common import (
    build_conversation_error,
    merge_metadata,
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


class SqlAlchemyConversationMessageRepository:
    """基于 SQLAlchemy Core 的 conversation message 写入仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 conversation message 仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """追加用户、系统或工具消息。

        :param command: 追加消息的命令 DTO。
        :return: 已写入或幂等命中的消息。
        :raises ConversationStoreError: 当 session 不存在、状态非法、锚点冲突或数据库不可用时抛出。
        """

        operation = ConversationOperation.APPEND_MESSAGE
        try:
            with self._engine.begin() as connection:
                return self._append_regular_message(
                    connection=connection,
                    command=command,
                    operation=operation,
                )
        except ConversationStoreError:
            raise
        except IntegrityError as exc:
            raise self._build_integrity_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="AppendMessage 命中数据库完整性约束",
            ) from exc
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="AppendMessage 数据库操作失败",
            ) from exc

    def create_assistant_message(
        self,
        command: CreateAssistantMessageCommandDto,
    ) -> CreateAssistantMessageResultDto:
        """创建助手消息容器。

        :param command: 创建助手消息容器的命令 DTO。
        :return: 已创建或幂等命中的助手消息容器。
        :raises ConversationStoreError: 当 session 不存在、状态非法、锚点冲突或数据库不可用时抛出。
        """

        operation = ConversationOperation.CREATE_ASSISTANT_MESSAGE
        try:
            with self._engine.begin() as connection:
                return self._create_assistant_message(
                    connection=connection,
                    command=command,
                    operation=operation,
                )
        except ConversationStoreError:
            raise
        except IntegrityError as exc:
            raise self._build_integrity_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="CreateAssistantMessage 命中数据库完整性约束",
            ) from exc
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="CreateAssistantMessage 数据库操作失败",
            ) from exc

    def append_assistant_segment(
        self,
        command: AppendAssistantSegmentCommandDto,
    ) -> AppendAssistantSegmentResultDto:
        """追加助手回复分段。

        :param command: 追加助手回复分段的命令 DTO。
        :return: 已写入或幂等命中的助手回复分段。
        :raises ConversationStoreError: 当消息不存在、状态非法、锚点冲突或数据库不可用时抛出。
        """

        operation = ConversationOperation.APPEND_ASSISTANT_SEGMENT
        try:
            with self._engine.begin() as connection:
                return self._append_assistant_segment(
                    connection=connection,
                    command=command,
                    operation=operation,
                )
        except ConversationStoreError:
            raise
        except IntegrityError as exc:
            return self._resolve_segment_after_integrity_error(
                command=command,
                insert_error=exc,
            )
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="AppendAssistantSegment 数据库操作失败",
            ) from exc

    def finalize_assistant_message(
        self,
        command: FinalizeAssistantMessageCommandDto,
    ) -> FinalizeAssistantMessageResultDto:
        """完成助手消息。

        :param command: 完成助手消息的命令 DTO。
        :return: 完成后的助手消息。
        :raises ConversationStoreError: 当消息不存在、状态非法、锚点冲突或数据库不可用时抛出。
        """

        operation = ConversationOperation.FINALIZE_ASSISTANT_MESSAGE
        try:
            with self._engine.begin() as connection:
                return self._finalize_assistant_message(
                    connection=connection,
                    command=command,
                    operation=operation,
                )
        except ConversationStoreError:
            raise
        except ValidationError as exc:
            raise self._build_validation_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                validation_error=exc,
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise self._build_timeout_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._build_store_error(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="FinalizeAssistantMessage 数据库操作失败",
            ) from exc

    def _append_regular_message(
        self,
        *,
        connection: Connection,
        command: AppendMessageCommandDto,
        operation: ConversationOperation,
    ) -> AppendMessageResultDto:
        """执行普通消息追加事务。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 追加消息的命令 DTO。
        :param operation: 当前 ConversationStore 操作名。
        :return: 已写入或幂等命中的消息。
        :raises ConversationStoreError: 当 session 或消息状态不满足写入条件时抛出。
        """

        if command.role is ConversationMessageRole.ASSISTANT:
            raise build_conversation_error(
                code=ConversationErrorCode.INVALID_ARGUMENT,
                operation=operation,
                message="AppendMessage 不接受 assistant 角色，请使用 CreateAssistantMessage",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={"role": command.role.value},
            )
        existing_message = self._fetch_message_by_idempotency_key(
            connection=connection,
            session_id=command.session_id,
            idempotency_key=command.idempotency_key,
        )
        if existing_message is not None:
            return AppendMessageResultDto(
                message=self._load_message_dto(
                    connection=connection,
                    row=existing_message,
                    include_segments=True,
                    include_attachments=True,
                ),
                idempotent=True,
            )
        session = self._require_writable_session(
            connection=connection,
            operation=operation,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        message_id = self._new_id(prefix="msg")
        sequence_no = session.next_sequence_no
        now = datetime.now(UTC)
        connection.execute(
            CONVERSATION_MESSAGE_TABLE.insert().values(
                message_id=message_id,
                session_id=command.session_id,
                user_id=command.user_id,
                pet_id=command.pet_id,
                role=command.role.value,
                content_type=command.content_type,
                content=command.content,
                sequence_no=sequence_no,
                status=ConversationMessageStatus.FINALIZED.value,
                reply_to_message_id=None,
                idempotency_key=command.idempotency_key,
                metadata=command.metadata,
                created_at=now,
                finalized_at=now,
            )
        )
        self._insert_attachment_refs(
            connection=connection,
            message_id=message_id,
            session_id=command.session_id,
            pet_id=command.pet_id,
            attachments=command.attachments,
            created_at=now,
        )
        self._advance_session_after_message(
            connection=connection,
            session_id=command.session_id,
            next_sequence_no=sequence_no + 1,
            now=now,
        )
        inserted_row = self._fetch_message_by_id(
            connection=connection,
            message_id=message_id,
            for_update=False,
        )
        if inserted_row is None:
            raise self._message_append_failed(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="新建 conversation_message 后无法读取已创建行",
                conflict_with={"message_id": message_id},
            )
        return AppendMessageResultDto(
            message=self._load_message_dto(
                connection=connection,
                row=inserted_row,
                include_segments=True,
                include_attachments=True,
            ),
            idempotent=False,
        )

    def _create_assistant_message(
        self,
        *,
        connection: Connection,
        command: CreateAssistantMessageCommandDto,
        operation: ConversationOperation,
    ) -> CreateAssistantMessageResultDto:
        """执行助手消息容器创建事务。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 创建助手消息容器的命令 DTO。
        :param operation: 当前 ConversationStore 操作名。
        :return: 已创建或幂等命中的助手消息容器。
        :raises ConversationStoreError: 当 session 不满足写入条件时抛出。
        """

        existing_message = self._fetch_message_by_idempotency_key(
            connection=connection,
            session_id=command.session_id,
            idempotency_key=command.idempotency_key,
        )
        if existing_message is not None:
            return CreateAssistantMessageResultDto(
                message=self._load_message_dto(
                    connection=connection,
                    row=existing_message,
                    include_segments=True,
                    include_attachments=True,
                ),
                idempotent=True,
            )
        session = self._require_writable_session(
            connection=connection,
            operation=operation,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        message_id = self._new_id(prefix="msg")
        sequence_no = session.next_sequence_no
        now = datetime.now(UTC)
        connection.execute(
            CONVERSATION_MESSAGE_TABLE.insert().values(
                message_id=message_id,
                session_id=command.session_id,
                user_id=command.user_id,
                pet_id=command.pet_id,
                role=ConversationMessageRole.ASSISTANT.value,
                content_type=command.content_type,
                content="",
                sequence_no=sequence_no,
                status=ConversationMessageStatus.STREAMING.value,
                reply_to_message_id=command.reply_to_message_id,
                idempotency_key=command.idempotency_key,
                metadata=command.metadata,
                created_at=now,
                finalized_at=None,
            )
        )
        self._advance_session_after_message(
            connection=connection,
            session_id=command.session_id,
            next_sequence_no=sequence_no + 1,
            now=now,
        )
        inserted_row = self._fetch_message_by_id(
            connection=connection,
            message_id=message_id,
            for_update=False,
        )
        if inserted_row is None:
            raise self._message_append_failed(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="新建 assistant conversation_message 后无法读取已创建行",
                conflict_with={"message_id": message_id},
            )
        return CreateAssistantMessageResultDto(
            message=self._load_message_dto(
                connection=connection,
                row=inserted_row,
                include_segments=True,
                include_attachments=True,
            ),
            idempotent=False,
        )

    def _append_assistant_segment(
        self,
        *,
        connection: Connection,
        command: AppendAssistantSegmentCommandDto,
        operation: ConversationOperation,
    ) -> AppendAssistantSegmentResultDto:
        """执行助手回复分段追加事务。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 追加助手回复分段的命令 DTO。
        :param operation: 当前 ConversationStore 操作名。
        :return: 已写入或幂等命中的助手回复分段。
        :raises ConversationStoreError: 当 session、message 或 segment 状态不满足写入条件时抛出。
        """

        existing_segment = self._fetch_segment_by_idempotency_key(
            connection=connection,
            message_id=command.message_id,
            idempotency_key=command.idempotency_key,
        )
        if existing_segment is not None:
            return AppendAssistantSegmentResultDto(
                segment=row_to_segment_dto(existing_segment),
                idempotent=True,
            )
        existing_order_segment = self._fetch_segment_by_order(
            connection=connection,
            message_id=command.message_id,
            segment_order=command.segment_order,
        )
        if existing_order_segment is not None:
            return AppendAssistantSegmentResultDto(
                segment=row_to_segment_dto(existing_order_segment),
                idempotent=True,
            )
        self._require_writable_session(
            connection=connection,
            operation=operation,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        message_row = self._fetch_message_by_id(
            connection=connection,
            message_id=command.message_id,
            for_update=True,
        )
        if message_row is None:
            raise self._message_not_found(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message_id=command.message_id,
            )
        message = row_to_message_dto(message_row)
        self._ensure_message_matches_command(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            message=message,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
        )
        if message.role is not ConversationMessageRole.ASSISTANT:
            raise self._message_invalid_state(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="只有 assistant 消息可以追加 segment",
                conflict_with={
                    "message_id": message.message_id,
                    "role": message.role.value,
                },
            )
        if message.status is ConversationMessageStatus.FINALIZED:
            raise build_conversation_error(
                code=ConversationErrorCode.MESSAGE_ALREADY_FINALIZED,
                operation=operation,
                message="assistant message 已完成，不能继续追加 segment",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={"message_id": message.message_id},
            )
        if message.status is not ConversationMessageStatus.STREAMING:
            raise self._message_invalid_state(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="assistant message 状态不允许追加 segment",
                conflict_with={
                    "message_id": message.message_id,
                    "status": message.status.value,
                },
            )
        segment_id = self._new_id(prefix="seg")
        now = datetime.now(UTC)
        connection.execute(
            CONVERSATION_MESSAGE_SEGMENT_TABLE.insert().values(
                segment_id=segment_id,
                message_id=command.message_id,
                session_id=command.session_id,
                pet_id=command.pet_id,
                segment_order=command.segment_order,
                content=command.content,
                idempotency_key=command.idempotency_key,
                metadata=command.metadata,
                published_at=now,
            )
        )
        inserted_row = self._fetch_segment_by_id(
            connection=connection,
            segment_id=segment_id,
        )
        if inserted_row is None:
            raise self._message_append_failed(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="新建 conversation_message_segment 后无法读取已创建行",
                conflict_with={"segment_id": segment_id},
            )
        return AppendAssistantSegmentResultDto(
            segment=row_to_segment_dto(inserted_row),
            idempotent=False,
        )

    def _finalize_assistant_message(
        self,
        *,
        connection: Connection,
        command: FinalizeAssistantMessageCommandDto,
        operation: ConversationOperation,
    ) -> FinalizeAssistantMessageResultDto:
        """执行助手消息完成事务。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 完成助手消息的命令 DTO。
        :param operation: 当前 ConversationStore 操作名。
        :return: 完成后的助手消息。
        :raises ConversationStoreError: 当消息状态不满足完成条件时抛出。
        """

        self._require_session_for_read_or_write(
            connection=connection,
            operation=operation,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        message_row = self._fetch_message_by_id(
            connection=connection,
            message_id=command.message_id,
            for_update=True,
        )
        if message_row is None:
            raise self._message_not_found(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message_id=command.message_id,
            )
        message = row_to_message_dto(message_row)
        self._ensure_message_matches_command(
            operation=operation,
            request_id=command.request_id,
            trace_id=command.trace_id,
            message=message,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
        )
        if message.role is not ConversationMessageRole.ASSISTANT:
            raise self._message_invalid_state(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="只有 assistant 消息可以执行 finalize",
                conflict_with={
                    "message_id": message.message_id,
                    "role": message.role.value,
                },
            )
        if message.status is ConversationMessageStatus.FINALIZED:
            return FinalizeAssistantMessageResultDto(
                message=self._load_message_dto(
                    connection=connection,
                    row=message_row,
                    include_segments=True,
                    include_attachments=True,
                ),
                idempotent=True,
            )
        if message.status is not ConversationMessageStatus.STREAMING:
            raise self._message_invalid_state(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message="assistant message 状态不允许完成",
                conflict_with={
                    "message_id": message.message_id,
                    "status": message.status.value,
                },
            )
        final_content = command.final_content
        if final_content is None:
            final_content = self._join_segment_content(
                connection=connection,
                message_id=command.message_id,
            )
        metadata = merge_metadata(
            original=message.metadata,
            patch=command.metadata_patch,
        )
        now = datetime.now(UTC)
        connection.execute(
            CONVERSATION_MESSAGE_TABLE.update()
            .where(CONVERSATION_MESSAGE_TABLE.c.message_id == command.message_id)
            .values(
                content=final_content,
                status=ConversationMessageStatus.FINALIZED.value,
                metadata=metadata,
                finalized_at=now,
            )
        )
        updated_row = self._fetch_message_by_id(
            connection=connection,
            message_id=command.message_id,
            for_update=False,
        )
        if updated_row is None:
            raise self._message_not_found(
                operation=operation,
                request_id=command.request_id,
                trace_id=command.trace_id,
                message_id=command.message_id,
            )
        return FinalizeAssistantMessageResultDto(
            message=self._load_message_dto(
                connection=connection,
                row=updated_row,
                include_segments=True,
                include_attachments=True,
            ),
            idempotent=False,
        )

    def _require_writable_session(
        self,
        *,
        connection: Connection,
        operation: ConversationOperation,
        session_id: str,
        user_id: str,
        pet_id: str,
        request_id: str,
        trace_id: str,
    ) -> ConversationSessionDto:
        """读取并校验可写 conversation session。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param operation: 当前 ConversationStore 操作名。
        :param session_id: 需要校验的 session ID。
        :param user_id: 请求方用户 ID。
        :param pet_id: 请求方宠物 ID。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 已校验的 ConversationSessionDto。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或状态不可写时抛出。
        """

        session = self._require_session_for_read_or_write(
            connection=connection,
            operation=operation,
            session_id=session_id,
            user_id=user_id,
            pet_id=pet_id,
            request_id=request_id,
            trace_id=trace_id,
            for_update=True,
        )
        if session.status is ConversationSessionStatus.ACTIVE:
            return session
        if session.status is ConversationSessionStatus.CLOSED:
            raise build_conversation_error(
                code=ConversationErrorCode.SESSION_CLOSED,
                operation=operation,
                message="conversation session 已关闭，不能继续写入",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"session_id": session_id},
            )
        raise build_conversation_error(
            code=ConversationErrorCode.SESSION_ARCHIVED,
            operation=operation,
            message="conversation session 已归档，不能继续写入",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={"session_id": session_id},
        )

    def _require_session_for_read_or_write(
        self,
        *,
        connection: Connection,
        operation: ConversationOperation,
        session_id: str,
        user_id: str,
        pet_id: str,
        request_id: str,
        trace_id: str,
        for_update: bool = False,
    ) -> ConversationSessionDto:
        """读取并校验 conversation session 锚点。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param operation: 当前 ConversationStore 操作名。
        :param session_id: 需要校验的 session ID。
        :param user_id: 请求方用户 ID。
        :param pet_id: 请求方宠物 ID。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param for_update: 是否请求数据库对命中的 session 行加行级锁。
        :return: 已校验的 ConversationSessionDto。
        :raises ConversationStoreError: 当 session 不存在或锚点冲突时抛出。
        """

        statement = CONVERSATION_SESSION_TABLE.select().where(
            CONVERSATION_SESSION_TABLE.c.session_id == session_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = connection.execute(statement).mappings().one_or_none()
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
        return session

    def _advance_session_after_message(
        self,
        *,
        connection: Connection,
        session_id: str,
        next_sequence_no: int,
        now: datetime,
    ) -> None:
        """推进 session 消息序号与最近消息时间。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param session_id: 需要更新的 session ID。
        :param next_sequence_no: 下一条消息应分配的 sequence_no。
        :param now: 当前写入时间。
        :return: None。
        """

        connection.execute(
            CONVERSATION_SESSION_TABLE.update()
            .where(CONVERSATION_SESSION_TABLE.c.session_id == session_id)
            .values(
                next_sequence_no=next_sequence_no,
                updated_at=now,
                last_message_at=now,
            )
        )

    def _insert_attachment_refs(
        self,
        *,
        connection: Connection,
        message_id: str,
        session_id: str,
        pet_id: str,
        attachments: list[MessageAttachmentRefInputDto],
        created_at: datetime,
    ) -> None:
        """批量写入消息附件引用。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 附件引用所属消息 ID。
        :param session_id: 附件引用所属 session ID。
        :param pet_id: 附件引用所属宠物 ID。
        :param attachments: 附件引用输入列表。
        :param created_at: 附件引用创建时间。
        :return: None。
        """

        for attachment in attachments:
            connection.execute(
                CONVERSATION_ATTACHMENT_REF_TABLE.insert().values(
                    attachment_ref_id=self._new_id(prefix="attref"),
                    attachment_id=attachment.attachment_id,
                    message_id=message_id,
                    session_id=session_id,
                    pet_id=pet_id,
                    attachment_type=attachment.attachment_type,
                    metadata=attachment.metadata,
                    created_at=created_at,
                )
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

    def _join_segment_content(
        self,
        *,
        connection: Connection,
        message_id: str,
    ) -> str:
        """拼接指定助手消息的分段正文。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 需要拼接分段的助手消息 ID。
        :return: 按 segment_order 升序拼接后的正文。
        """

        segments = self._list_segments(connection=connection, message_id=message_id)
        return "".join(segment.content for segment in segments)

    def _fetch_message_by_id(
        self,
        *,
        connection: Connection,
        message_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        """按 message_id 精确读取消息。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 需要读取的消息 ID。
        :param for_update: 是否请求数据库对命中的行加行级锁。
        :return: 命中的 conversation_message 行；不存在时返回 None。
        """

        statement = CONVERSATION_MESSAGE_TABLE.select().where(
            CONVERSATION_MESSAGE_TABLE.c.message_id == message_id
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one_or_none()

    def _fetch_message_by_idempotency_key(
        self,
        *,
        connection: Connection,
        session_id: str,
        idempotency_key: str | None,
    ) -> RowMapping | None:
        """按消息幂等键读取既有消息。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param session_id: 幂等键所属 session ID。
        :param idempotency_key: 可选消息幂等键；为空时不查询。
        :return: 命中的 conversation_message 行；不存在时返回 None。
        """

        if idempotency_key is None:
            return None
        return (
            connection.execute(
                CONVERSATION_MESSAGE_TABLE.select()
                .where(CONVERSATION_MESSAGE_TABLE.c.session_id == session_id)
                .where(
                    CONVERSATION_MESSAGE_TABLE.c.idempotency_key == idempotency_key
                )
            )
            .mappings()
            .one_or_none()
        )

    def _fetch_segment_by_id(
        self,
        *,
        connection: Connection,
        segment_id: str,
    ) -> RowMapping | None:
        """按 segment_id 精确读取助手回复分段。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param segment_id: 需要读取的分段 ID。
        :return: 命中的 conversation_message_segment 行；不存在时返回 None。
        """

        return (
            connection.execute(
                CONVERSATION_MESSAGE_SEGMENT_TABLE.select().where(
                    CONVERSATION_MESSAGE_SEGMENT_TABLE.c.segment_id == segment_id
                )
            )
            .mappings()
            .one_or_none()
        )

    def _fetch_segment_by_idempotency_key(
        self,
        *,
        connection: Connection,
        message_id: str,
        idempotency_key: str | None,
    ) -> RowMapping | None:
        """按助手分段幂等键读取既有分段。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 分段所属助手消息 ID。
        :param idempotency_key: 可选分段幂等键；为空时不查询。
        :return: 命中的 conversation_message_segment 行；不存在时返回 None。
        """

        if idempotency_key is None:
            return None
        return (
            connection.execute(
                CONVERSATION_MESSAGE_SEGMENT_TABLE.select()
                .where(CONVERSATION_MESSAGE_SEGMENT_TABLE.c.message_id == message_id)
                .where(
                    CONVERSATION_MESSAGE_SEGMENT_TABLE.c.idempotency_key
                    == idempotency_key
                )
            )
            .mappings()
            .one_or_none()
        )

    def _fetch_segment_by_order(
        self,
        *,
        connection: Connection,
        message_id: str,
        segment_order: int,
    ) -> RowMapping | None:
        """按助手消息和分段序号读取既有分段。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param message_id: 分段所属助手消息 ID。
        :param segment_order: 分段业务排序。
        :return: 命中的 conversation_message_segment 行；不存在时返回 None。
        """

        return (
            connection.execute(
                CONVERSATION_MESSAGE_SEGMENT_TABLE.select()
                .where(CONVERSATION_MESSAGE_SEGMENT_TABLE.c.message_id == message_id)
                .where(
                    CONVERSATION_MESSAGE_SEGMENT_TABLE.c.segment_order
                    == segment_order
                )
            )
            .mappings()
            .one_or_none()
        )

    def _resolve_segment_after_integrity_error(
        self,
        *,
        command: AppendAssistantSegmentCommandDto,
        insert_error: IntegrityError,
    ) -> AppendAssistantSegmentResultDto:
        """在分段插入完整性冲突后重读既有分段。

        :param command: 追加助手回复分段的命令 DTO。
        :param insert_error: 捕获到的数据库完整性错误。
        :return: 幂等命中的助手回复分段。
        :raises ConversationStoreError: 当冲突后无法定位既有分段时抛出。
        """

        with self._engine.begin() as connection:
            existing_segment = self._fetch_segment_by_idempotency_key(
                connection=connection,
                message_id=command.message_id,
                idempotency_key=command.idempotency_key,
            )
            if existing_segment is None:
                existing_segment = self._fetch_segment_by_order(
                    connection=connection,
                    message_id=command.message_id,
                    segment_order=command.segment_order,
                )
            if existing_segment is not None:
                return AppendAssistantSegmentResultDto(
                    segment=row_to_segment_dto(existing_segment),
                    idempotent=True,
                )
        raise self._build_integrity_error(
            operation=ConversationOperation.APPEND_ASSISTANT_SEGMENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
            message="AppendAssistantSegment 命中数据库完整性约束且无法重读既有分段",
        ) from insert_error

    def _ensure_message_matches_command(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message: ConversationMessageDto,
        session_id: str,
        user_id: str,
        pet_id: str,
    ) -> None:
        """校验消息锚点与请求一致。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message: 已读取的消息 DTO。
        :param session_id: 请求携带的 session ID。
        :param user_id: 请求携带的 user ID。
        :param pet_id: 请求携带的 pet ID。
        :return: None。
        :raises ConversationStoreError: 当消息锚点与请求不一致时抛出。
        """

        if message.session_id != session_id:
            raise self._message_invalid_state(
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
                message="message 不属于请求 session",
                conflict_with={
                    "message_id": message.message_id,
                    "message_session_id": message.session_id,
                    "requested_session_id": session_id,
                },
            )
        if message.user_id != user_id:
            raise build_conversation_error(
                code=ConversationErrorCode.SESSION_USER_CONFLICT,
                operation=operation,
                message="message user_id 与请求 user_id 不一致",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={
                    "message_id": message.message_id,
                    "message_user_id": message.user_id,
                    "requested_user_id": user_id,
                },
            )
        if message.pet_id != pet_id:
            raise build_conversation_error(
                code=ConversationErrorCode.SESSION_PET_CONFLICT,
                operation=operation,
                message="message pet_id 与请求 pet_id 不一致",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={
                    "message_id": message.message_id,
                    "message_pet_id": message.pet_id,
                    "requested_pet_id": pet_id,
                },
            )

    def _message_not_found(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message_id: str,
    ) -> ConversationStoreError:
        """构建消息不存在错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message_id: 未命中的消息 ID。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.MESSAGE_NOT_FOUND,
            operation=operation,
            message="conversation message 不存在",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={"message_id": message_id},
        )

    def _message_invalid_state(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message: str,
        conflict_with: dict[str, object],
    ) -> ConversationStoreError:
        """构建消息状态非法错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message: 面向工程排障的错误说明。
        :param conflict_with: 冲突对象摘要。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.MESSAGE_INVALID_STATE,
            operation=operation,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with=conflict_with,
        )

    def _message_append_failed(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message: str,
        conflict_with: dict[str, object],
    ) -> ConversationStoreError:
        """构建消息写入失败错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message: 面向工程排障的错误说明。
        :param conflict_with: 冲突对象摘要。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.MESSAGE_APPEND_FAILED,
            operation=operation,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
            conflict_with=conflict_with,
        )

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

    def _build_integrity_error(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        message: str,
    ) -> ConversationStoreError:
        """构建数据库完整性约束错误。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param message: 面向工程排障的错误说明。
        :return: ConversationStore 领域异常对象。
        """

        return build_conversation_error(
            code=ConversationErrorCode.MESSAGE_APPEND_FAILED,
            operation=operation,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
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

    def _new_id(
        self,
        *,
        prefix: str,
    ) -> str:
        """生成 ConversationStore 内部 ID。

        :param prefix: ID 前缀。
        :return: 带前缀的唯一 ID 字符串。
        """

        return f"{prefix}_{uuid4().hex}"


__all__: tuple[str, ...] = (
    "SqlAlchemyConversationMessageRepository",
)
