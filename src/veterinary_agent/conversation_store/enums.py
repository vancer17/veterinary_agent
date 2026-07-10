##################################################################################################
# 文件: src/veterinary_agent/conversation_store/enums.py
# 作用: 定义 ConversationStore 组件的稳定字符串枚举，供接口契约、错误映射、日志和存储实现复用。
# 边界: 仅描述 L0 ConversationStore 领域枚举，不包含数据库访问、业务策略或 Agent 编排逻辑。
##################################################################################################

from enum import StrEnum


class ConversationErrorCode(StrEnum):
    """ConversationStore 稳定错误码。"""

    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_CLOSED = "SESSION_CLOSED"
    SESSION_ARCHIVED = "SESSION_ARCHIVED"
    SESSION_PET_CONFLICT = "SESSION_PET_CONFLICT"
    SESSION_USER_CONFLICT = "SESSION_USER_CONFLICT"
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
    MESSAGE_DUPLICATE = "MESSAGE_DUPLICATE"
    MESSAGE_APPEND_FAILED = "MESSAGE_APPEND_FAILED"
    MESSAGE_ALREADY_FINALIZED = "MESSAGE_ALREADY_FINALIZED"
    MESSAGE_INVALID_STATE = "MESSAGE_INVALID_STATE"
    MESSAGE_TOO_LARGE = "MESSAGE_TOO_LARGE"
    METADATA_TOO_LARGE = "METADATA_TOO_LARGE"
    ATTACHMENT_LIMIT_EXCEEDED = "ATTACHMENT_LIMIT_EXCEEDED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    OPERATION_TIMEOUT = "OPERATION_TIMEOUT"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class ConversationOperation(StrEnum):
    """ConversationStore 对外接口操作名。"""

    ENSURE_SESSION = "EnsureSession"
    GET_SESSION = "GetSession"
    APPEND_MESSAGE = "AppendMessage"
    CREATE_ASSISTANT_MESSAGE = "CreateAssistantMessage"
    APPEND_ASSISTANT_SEGMENT = "AppendAssistantSegment"
    FINALIZE_ASSISTANT_MESSAGE = "FinalizeAssistantMessage"
    LIST_MESSAGES_BY_SESSION = "ListMessagesBySession"
    GET_RECENT_MESSAGES = "GetRecentMessages"
    CLOSE_SESSION = "CloseSession"
    ARCHIVE_SESSION = "ArchiveSession"


class ConversationSessionStatus(StrEnum):
    """conversation session 生命周期状态。"""

    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class ConversationMessageRole(StrEnum):
    """conversation message 角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationMessageStatus(StrEnum):
    """conversation message 生命周期状态。"""

    FINALIZED = "finalized"
    STREAMING = "streaming"
    CANCELLED = "cancelled"


__all__: tuple[str, ...] = (
    "ConversationErrorCode",
    "ConversationMessageRole",
    "ConversationMessageStatus",
    "ConversationOperation",
    "ConversationSessionStatus",
)
