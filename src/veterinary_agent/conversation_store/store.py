##################################################################################################
# 文件: src/veterinary_agent/conversation_store/store.py
# 作用: 定义 ConversationStore 应用内服务接口契约，并提供领域外依赖尚未接入时的 TODO 空壳实现。
# 边界: 仅声明对话事实存储组件的稳定入口，不实现数据库、RuntimeConfig、事件总线或业务组件集成。
##################################################################################################

from typing import NoReturn, Protocol

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
    UpdateSessionStatusResultDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationOperation,
)
from veterinary_agent.conversation_store.errors import ConversationStoreError


class ConversationStore(Protocol):
    """ConversationStore 应用内服务接口契约。"""

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认 conversation session。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前请求命中的 session 与创建标记。
        :raises ConversationStoreError: 当 user_id/pet_id 冲突、入参无效或存储不可用时抛出。
        """

        ...

    async def get_session(
        self,
        query: GetSessionQueryDto,
    ) -> ConversationSessionDto:
        """读取 conversation session。

        :param query: 读取 conversation session 的查询 DTO。
        :return: 命中的 conversation session。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或存储不可用时抛出。
        """

        ...

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """追加用户、系统或工具消息。

        :param command: 追加消息的命令 DTO。
        :return: 已写入或幂等命中的消息。
        :raises ConversationStoreError: 当 session 不存在、已关闭、锚点冲突、消息过大或存储不可用时抛出。
        """

        ...

    async def create_assistant_message(
        self,
        command: CreateAssistantMessageCommandDto,
    ) -> CreateAssistantMessageResultDto:
        """创建助手消息容器。

        :param command: 创建助手消息容器的命令 DTO。
        :return: 已创建或幂等命中的助手消息容器。
        :raises ConversationStoreError: 当 session 不存在、已关闭、锚点冲突或存储不可用时抛出。
        """

        ...

    async def append_assistant_segment(
        self,
        command: AppendAssistantSegmentCommandDto,
    ) -> AppendAssistantSegmentResultDto:
        """追加助手回复分段。

        :param command: 追加助手回复分段的命令 DTO。
        :return: 已写入或幂等命中的助手回复分段。
        :raises ConversationStoreError: 当助手消息不存在、已完成、锚点冲突、分段过大或存储不可用时抛出。
        """

        ...

    async def finalize_assistant_message(
        self,
        command: FinalizeAssistantMessageCommandDto,
    ) -> FinalizeAssistantMessageResultDto:
        """完成助手消息。

        :param command: 完成助手消息的命令 DTO。
        :return: 完成后的助手消息。
        :raises ConversationStoreError: 当助手消息不存在、状态非法、锚点冲突或存储不可用时抛出。
        """

        ...

    async def list_messages_by_session(
        self,
        query: ListMessagesBySessionQueryDto,
    ) -> ListMessagesBySessionResultDto:
        """按 session 分页查询消息。

        :param query: 按 session 分页查询消息的查询 DTO。
        :return: 按 sequence_no 升序返回的消息分页。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突、分页参数非法或存储不可用时抛出。
        """

        ...

    async def get_recent_messages(
        self,
        query: GetRecentMessagesQueryDto,
    ) -> GetRecentMessagesResultDto:
        """读取面向上下文构建的最近消息。

        :param query: 读取最近消息的查询 DTO。
        :return: 按 sequence_no 升序返回的最近消息列表。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突、分页参数非法或存储不可用时抛出。
        """

        ...

    async def close_session(
        self,
        command: CloseSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """关闭 conversation session。

        :param command: 关闭 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或存储不可用时抛出。
        """

        ...

    async def archive_session(
        self,
        command: ArchiveSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """归档 conversation session。

        :param command: 归档 conversation session 的命令 DTO。
        :return: 更新后的 session 与幂等标记。
        :raises ConversationStoreError: 当 session 不存在、锚点冲突或存储不可用时抛出。
        """

        ...


class TodoConversationStore:
    """领域外依赖尚未接入时使用的 ConversationStore TODO 空壳实现。"""

    def _raise_unavailable(
        self,
        *,
        operation: ConversationOperation,
        request_id: str,
        trace_id: str,
    ) -> NoReturn:
        """抛出 ConversationStore 实现尚未接入的占位错误。

        :param operation: 当前被调用的 ConversationStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 该函数总是抛出异常，不会返回。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        raise ConversationStoreError(
            code=ConversationErrorCode.STORE_UNAVAILABLE,
            operation=operation,
            message="ConversationStore 领域外依赖尚未接入",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认 conversation session 的 TODO 占位实现。

        :param command: 创建或确认 conversation session 的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.ENSURE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def get_session(
        self,
        query: GetSessionQueryDto,
    ) -> ConversationSessionDto:
        """读取 conversation session 的 TODO 占位实现。

        :param query: 读取 conversation session 的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.GET_SESSION,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """追加消息的 TODO 占位实现。

        :param command: 追加消息的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.APPEND_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def create_assistant_message(
        self,
        command: CreateAssistantMessageCommandDto,
    ) -> CreateAssistantMessageResultDto:
        """创建助手消息容器的 TODO 占位实现。

        :param command: 创建助手消息容器的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.CREATE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def append_assistant_segment(
        self,
        command: AppendAssistantSegmentCommandDto,
    ) -> AppendAssistantSegmentResultDto:
        """追加助手回复分段的 TODO 占位实现。

        :param command: 追加助手回复分段的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.APPEND_ASSISTANT_SEGMENT,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def finalize_assistant_message(
        self,
        command: FinalizeAssistantMessageCommandDto,
    ) -> FinalizeAssistantMessageResultDto:
        """完成助手消息的 TODO 占位实现。

        :param command: 完成助手消息的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.FINALIZE_ASSISTANT_MESSAGE,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def list_messages_by_session(
        self,
        query: ListMessagesBySessionQueryDto,
    ) -> ListMessagesBySessionResultDto:
        """分页查询消息的 TODO 占位实现。

        :param query: 按 session 分页查询消息的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.LIST_MESSAGES_BY_SESSION,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def get_recent_messages(
        self,
        query: GetRecentMessagesQueryDto,
    ) -> GetRecentMessagesResultDto:
        """读取最近消息的 TODO 占位实现。

        :param query: 读取最近消息的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.GET_RECENT_MESSAGES,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def close_session(
        self,
        command: CloseSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """关闭 session 的 TODO 占位实现。

        :param command: 关闭 conversation session 的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.CLOSE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def archive_session(
        self,
        command: ArchiveSessionCommandDto,
    ) -> UpdateSessionStatusResultDto:
        """归档 session 的 TODO 占位实现。

        :param command: 归档 conversation session 的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises ConversationStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=ConversationOperation.ARCHIVE_SESSION,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )


__all__: tuple[str, ...] = (
    "ConversationStore",
    "TodoConversationStore",
)
