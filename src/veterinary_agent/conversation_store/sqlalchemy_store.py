##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_store.py
# 作用: 提供基于 SQLAlchemy 的 ConversationStore facade，装配 session、message、segment 和读取仓储，
#       并在服务层执行 RuntimeConfig 限制、操作超时与统一错误映射。
# 边界: 仅访问 ConversationStore 自有表和配置对象；不实现业务策略、GraphRuntime、RAG、模型调用或事件总线。
##################################################################################################

import asyncio
from typing import Awaitable, TypeVar

from sqlalchemy import create_engine

from veterinary_agent.config import (
    ConversationStoreSettings,
    load_conversation_store_settings,
)
from veterinary_agent.conversation_store.dto import (
    AppendAssistantSegmentCommandDto,
    AppendAssistantSegmentResultDto,
    AppendMessageCommandDto,
    AppendMessageResultDto,
    ArchiveSessionCommandDto,
    CloseSessionCommandDto,
    ConversationSessionDto,
    CreateAssistantMessageCommandDto,
    CreateAssistantMessageResultDto,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    FinalizeAssistantMessageCommandDto,
    FinalizeAssistantMessageResultDto,
    GetRecentMessagesQueryDto,
    GetRecentMessagesResultDto,
    GetSessionQueryDto,
    ListMessagesBySessionQueryDto,
    ListMessagesBySessionResultDto,
    MessageAttachmentRefInputDto,
    UpdateSessionStatusResultDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationMessageRole,
    ConversationOperation,
)
from veterinary_agent.conversation_store.sqlalchemy_common import (
    build_conversation_error,
    measure_json_bytes,
    measure_text_bytes,
)
from veterinary_agent.conversation_store.sqlalchemy_message import (
    SqlAlchemyConversationMessageRepository,
)
from veterinary_agent.conversation_store.sqlalchemy_read import (
    SqlAlchemyConversationReadRepository,
)
from veterinary_agent.conversation_store.sqlalchemy_session import (
    SqlAlchemyConversationSessionRepository,
)
from veterinary_agent.conversation_store.sqlalchemy_tables import (
    CONVERSATION_ATTACHMENT_REF_TABLE,
    CONVERSATION_MESSAGE_SEGMENT_TABLE,
    CONVERSATION_MESSAGE_TABLE,
    CONVERSATION_SESSION_TABLE,
    CONVERSATION_STORE_METADATA,
)
from veterinary_agent.conversation_store.store import TodoConversationStore

_T = TypeVar("_T")


class SqlAlchemyConversationStore(TodoConversationStore):
    """基于 SQLAlchemy 仓储的 ConversationStore 实现。"""

    def __init__(
        self,
        *,
        session_repository: SqlAlchemyConversationSessionRepository,
        message_repository: SqlAlchemyConversationMessageRepository,
        read_repository: SqlAlchemyConversationReadRepository,
        settings: ConversationStoreSettings,
    ) -> None:
        """初始化 SQLAlchemy ConversationStore。

        :param session_repository: conversation_session 仓储实例。
        :param message_repository: conversation_message 和 segment 写入仓储实例。
        :param read_repository: conversation 读取侧仓储实例。
        :param settings: ConversationStore RuntimeConfig 快照。
        :return: None。
        """

        self._session_repository = session_repository
        self._message_repository = message_repository
        self._read_repository = read_repository
        self._settings = settings

    def dispose(self) -> None:
        """释放 ConversationStore 持有的底层数据库资源。

        :return: None。
        """

        self._session_repository.dispose()

    async def _with_operation_timeout(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        awaitable: Awaitable[_T],
    ) -> _T:
        """按 RuntimeConfig 操作超时预算等待异步调用完成。

        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param awaitable: 需要受超时预算约束的异步调用。
        :return: 异步调用返回值。
        :raises ConversationStoreError: 当等待超过配置的操作超时时间时抛出。
        """

        try:
            return await asyncio.wait_for(
                awaitable,
                timeout=self._settings.operation_timeout_seconds,
            )
        except TimeoutError as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.OPERATION_TIMEOUT,
                operation=operation,
                message="ConversationStore 操作超过 RuntimeConfig 配置的超时预算",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={
                    "operation_timeout_seconds": (
                        self._settings.operation_timeout_seconds
                    )
                },
            ) from exc

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认 conversation session。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前请求命中的 session 与创建标记。
        :raises ConversationStoreError: 当锚点冲突、metadata 过大或存储不可用时抛出。
        """

        self._validate_metadata_size(
            metadata=command.metadata,
            operation=ConversationOperation.ENSURE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.ENSURE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._session_repository.ensure_session,
                command,
            ),
        )

    async def get_session(
        self,
        query: GetSessionQueryDto,
    ) -> ConversationSessionDto:
        """读取 conversation session。

        :param query: 读取 conversation session 的查询 DTO。
        :return: 命中的 conversation session。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或存储不可用时抛出。
        """

        return await self._with_operation_timeout(
            operation=ConversationOperation.GET_SESSION,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(self._session_repository.get_session, query),
        )

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """追加用户、系统或工具消息。

        :param command: 追加消息的命令 DTO。
        :return: 已写入或幂等命中的消息。
        :raises ConversationStoreError: 当消息大小、metadata、附件数量或存储状态不满足要求时抛出。
        """

        self._validate_regular_message_command(command=command)
        return await self._with_operation_timeout(
            operation=ConversationOperation.APPEND_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._message_repository.append_message,
                command,
            ),
        )

    async def create_assistant_message(
        self,
        command: CreateAssistantMessageCommandDto,
    ) -> CreateAssistantMessageResultDto:
        """创建助手消息容器。

        :param command: 创建助手消息容器的命令 DTO。
        :return: 已创建或幂等命中的助手消息容器。
        :raises ConversationStoreError: 当 metadata 过大或存储状态不满足要求时抛出。
        """

        self._validate_metadata_size(
            metadata=command.metadata,
            operation=ConversationOperation.CREATE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.CREATE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._message_repository.create_assistant_message,
                command,
            ),
        )

    async def append_assistant_segment(
        self,
        command: AppendAssistantSegmentCommandDto,
    ) -> AppendAssistantSegmentResultDto:
        """追加助手回复分段。

        :param command: 追加助手回复分段的命令 DTO。
        :return: 已写入或幂等命中的助手回复分段。
        :raises ConversationStoreError: 当分段大小、metadata 或存储状态不满足要求时抛出。
        """

        self._validate_segment_command(command=command)
        return await self._with_operation_timeout(
            operation=ConversationOperation.APPEND_ASSISTANT_SEGMENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._message_repository.append_assistant_segment,
                command,
            ),
        )

    async def finalize_assistant_message(
        self,
        command: FinalizeAssistantMessageCommandDto,
    ) -> FinalizeAssistantMessageResultDto:
        """完成助手消息。

        :param command: 完成助手消息的命令 DTO。
        :return: 完成后的助手消息。
        :raises ConversationStoreError: 当最终正文大小、metadata 或存储状态不满足要求时抛出。
        """

        self._validate_finalize_command(command=command)
        return await self._with_operation_timeout(
            operation=ConversationOperation.FINALIZE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._message_repository.finalize_assistant_message,
                command,
            ),
        )

    async def list_messages_by_session(
        self,
        query: ListMessagesBySessionQueryDto,
    ) -> ListMessagesBySessionResultDto:
        """按 session 分页查询消息。

        :param query: 按 session 分页查询消息的查询 DTO。
        :return: 按 sequence_no 升序返回的消息分页。
        :raises ConversationStoreError: 当分页大小超过配置、session 冲突或存储不可用时抛出。
        """

        self._validate_list_limit(
            requested_limit=query.limit,
            max_limit=self._settings.history.max_list_limit,
            operation=ConversationOperation.LIST_MESSAGES_BY_SESSION,
            request_id=query.request_id,
            trace_id=query.trace_id,
            owner={"session_id": query.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.LIST_MESSAGES_BY_SESSION,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.list_messages_by_session,
                query,
            ),
        )

    async def get_recent_messages(
        self,
        query: GetRecentMessagesQueryDto,
    ) -> GetRecentMessagesResultDto:
        """读取面向上下文构建的最近消息。

        :param query: 读取最近消息的查询 DTO。
        :return: 按 sequence_no 升序返回的最近消息列表。
        :raises ConversationStoreError: 当分页大小超过配置、session 冲突或存储不可用时抛出。
        """

        self._validate_list_limit(
            requested_limit=query.limit,
            max_limit=self._settings.history.max_recent_messages,
            operation=ConversationOperation.GET_RECENT_MESSAGES,
            request_id=query.request_id,
            trace_id=query.trace_id,
            owner={"session_id": query.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.GET_RECENT_MESSAGES,
            request_id=query.request_id,
            trace_id=query.trace_id,
            awaitable=asyncio.to_thread(
                self._read_repository.get_recent_messages,
                query,
            ),
        )

    async def close_session(
        self,
        command: CloseSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """关闭 conversation session。

        :param command: 关闭 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 metadata 过大、session 冲突或存储不可用时抛出。
        """

        self._validate_metadata_size(
            metadata=command.metadata_patch,
            operation=ConversationOperation.CLOSE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.CLOSE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._session_repository.close_session,
                command,
            ),
        )

    async def archive_session(
        self,
        command: ArchiveSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """归档 conversation session。

        :param command: 归档 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 metadata 过大、session 冲突或存储不可用时抛出。
        """

        self._validate_metadata_size(
            metadata=command.metadata_patch,
            operation=ConversationOperation.ARCHIVE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        return await self._with_operation_timeout(
            operation=ConversationOperation.ARCHIVE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
            awaitable=asyncio.to_thread(
                self._session_repository.archive_session,
                command,
            ),
        )

    def _validate_regular_message_command(
        self,
        *,
        command: AppendMessageCommandDto,
    ) -> None:
        """校验普通消息写入命令的 RuntimeConfig 限制。

        :param command: 追加消息的命令 DTO。
        :return: None。
        :raises ConversationStoreError: 当消息角色、正文大小、metadata 或附件数量不满足配置时抛出。
        """

        if command.role is ConversationMessageRole.ASSISTANT:
            raise build_conversation_error(
                code=ConversationErrorCode.INVALID_ARGUMENT,
                operation=ConversationOperation.APPEND_MESSAGE,
                message="AppendMessage 不接受 assistant 角色",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={"role": command.role.value},
            )
        self._validate_text_size(
            value=command.content,
            max_bytes=self._settings.message.max_message_bytes,
            code=ConversationErrorCode.MESSAGE_TOO_LARGE,
            operation=ConversationOperation.APPEND_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        self._validate_metadata_size(
            metadata=command.metadata,
            operation=ConversationOperation.APPEND_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )
        self._validate_attachment_refs(
            attachments=command.attachments,
            operation=ConversationOperation.APPEND_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"session_id": command.session_id},
        )

    def _validate_segment_command(
        self,
        *,
        command: AppendAssistantSegmentCommandDto,
    ) -> None:
        """校验助手分段写入命令的 RuntimeConfig 限制。

        :param command: 追加助手回复分段的命令 DTO。
        :return: None。
        :raises ConversationStoreError: 当分段正文或 metadata 不满足配置时抛出。
        """

        self._validate_text_size(
            value=command.content,
            max_bytes=self._settings.message.max_segment_bytes,
            code=ConversationErrorCode.MESSAGE_TOO_LARGE,
            operation=ConversationOperation.APPEND_ASSISTANT_SEGMENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"message_id": command.message_id},
        )
        self._validate_metadata_size(
            metadata=command.metadata,
            operation=ConversationOperation.APPEND_ASSISTANT_SEGMENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"message_id": command.message_id},
        )

    def _validate_finalize_command(
        self,
        *,
        command: FinalizeAssistantMessageCommandDto,
    ) -> None:
        """校验助手消息完成命令的 RuntimeConfig 限制。

        :param command: 完成助手消息的命令 DTO。
        :return: None。
        :raises ConversationStoreError: 当最终正文或 metadata patch 不满足配置时抛出。
        """

        if command.final_content is not None:
            self._validate_text_size(
                value=command.final_content,
                max_bytes=self._settings.message.max_message_bytes,
                code=ConversationErrorCode.MESSAGE_TOO_LARGE,
                operation=ConversationOperation.FINALIZE_ASSISTANT_MESSAGE,
                request_id=command.request_id,
                trace_id=command.trace_id,
                owner={"message_id": command.message_id},
            )
        self._validate_metadata_size(
            metadata=command.metadata_patch,
            operation=ConversationOperation.FINALIZE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
            owner={"message_id": command.message_id},
        )

    def _validate_text_size(
        self,
        *,
        value: str,
        max_bytes: int,
        code: ConversationErrorCode,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        owner: dict[str, object],
    ) -> None:
        """校验文本 UTF-8 字节大小。

        :param value: 需要校验的文本值。
        :param max_bytes: 允许的最大 UTF-8 字节数。
        :param code: 超限时使用的 ConversationStore 错误码。
        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param owner: 当前校验对象摘要。
        :return: None。
        :raises ConversationStoreError: 当文本大小超过配置上限时抛出。
        """

        size_bytes = measure_text_bytes(value)
        if size_bytes <= max_bytes:
            return
        raise build_conversation_error(
            code=code,
            operation=operation,
            message="ConversationStore 文本内容超出 RuntimeConfig 字节上限",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                **owner,
                "size_bytes": size_bytes,
                "max_bytes": max_bytes,
            },
        )

    def _validate_metadata_size(
        self,
        *,
        metadata: dict[str, object],
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        owner: dict[str, object],
    ) -> None:
        """校验 metadata JSON 字节大小。

        :param metadata: 需要校验的 metadata 映射。
        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param owner: 当前校验对象摘要。
        :return: None。
        :raises ConversationStoreError: 当 metadata 不是合法 JSON 值或超过配置上限时抛出。
        """

        try:
            size_bytes = measure_json_bytes(metadata)
        except (TypeError, ValueError) as exc:
            raise build_conversation_error(
                code=ConversationErrorCode.INVALID_ARGUMENT,
                operation=operation,
                message="metadata 不是合法 JSON 值",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={**owner, "reason": str(exc)},
            ) from exc
        max_bytes = self._settings.message.max_metadata_bytes
        if size_bytes <= max_bytes:
            return
        raise build_conversation_error(
            code=ConversationErrorCode.METADATA_TOO_LARGE,
            operation=operation,
            message="metadata 超出 RuntimeConfig 字节上限",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                **owner,
                "metadata_size_bytes": size_bytes,
                "max_metadata_bytes": max_bytes,
            },
        )

    def _validate_attachment_refs(
        self,
        *,
        attachments: list[MessageAttachmentRefInputDto],
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        owner: dict[str, object],
    ) -> None:
        """校验附件引用数量和 metadata 大小。

        :param attachments: 附件引用输入列表。
        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param owner: 当前校验对象摘要。
        :return: None。
        :raises ConversationStoreError: 当附件数量或 metadata 大小超过配置上限时抛出。
        """

        max_count = self._settings.message.max_attachment_refs_per_message
        if len(attachments) > max_count:
            raise build_conversation_error(
                code=ConversationErrorCode.ATTACHMENT_LIMIT_EXCEEDED,
                operation=operation,
                message="附件引用数量超出 RuntimeConfig 上限",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={
                    **owner,
                    "attachment_count": len(attachments),
                    "max_attachment_refs_per_message": max_count,
                },
            )
        for index, attachment in enumerate(attachments):
            self._validate_metadata_size(
                metadata=attachment.metadata,
                operation=operation,
                request_id=request_id,
                trace_id=trace_id,
                owner={**owner, "attachment_index": index},
            )

    def _validate_list_limit(
        self,
        *,
        requested_limit: int,
        max_limit: int,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
        owner: dict[str, object],
    ) -> None:
        """校验读取接口分页大小。

        :param requested_limit: 调用方请求的分页大小。
        :param max_limit: RuntimeConfig 允许的分页大小上限。
        :param operation: 当前 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param owner: 当前查询对象摘要。
        :return: None。
        :raises ConversationStoreError: 当分页大小超过配置上限时抛出。
        """

        if requested_limit <= max_limit:
            return
        raise build_conversation_error(
            code=ConversationErrorCode.INVALID_ARGUMENT,
            operation=operation,
            message="读取消息 limit 超出 RuntimeConfig 允许范围",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                **owner,
                "requested_limit": requested_limit,
                "max_limit": max_limit,
            },
        )


def create_sqlalchemy_conversation_store(
    database_url: str,
    *,
    settings: ConversationStoreSettings | None = None,
) -> SqlAlchemyConversationStore:
    """创建基于 SQLAlchemy 的 ConversationStore 实例。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param settings: 可选 ConversationStore RuntimeConfig；未传入时从默认配置源加载。
    :return: 已装配仓储与 RuntimeConfig 的 ConversationStore 实例。
    """

    resolved_settings = (
        load_conversation_store_settings() if settings is None else settings
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    session_repository = SqlAlchemyConversationSessionRepository(engine=engine)
    message_repository = SqlAlchemyConversationMessageRepository(engine=engine)
    read_repository = SqlAlchemyConversationReadRepository(engine=engine)
    return SqlAlchemyConversationStore(
        session_repository=session_repository,
        message_repository=message_repository,
        read_repository=read_repository,
        settings=resolved_settings,
    )


__all__: tuple[str, ...] = (
    "CONVERSATION_ATTACHMENT_REF_TABLE",
    "CONVERSATION_MESSAGE_SEGMENT_TABLE",
    "CONVERSATION_MESSAGE_TABLE",
    "CONVERSATION_SESSION_TABLE",
    "CONVERSATION_STORE_METADATA",
    "SqlAlchemyConversationMessageRepository",
    "SqlAlchemyConversationReadRepository",
    "SqlAlchemyConversationSessionRepository",
    "SqlAlchemyConversationStore",
    "create_sqlalchemy_conversation_store",
)
