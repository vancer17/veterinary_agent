##################################################################################################
# 文件: src/veterinary_agent/conversation_store/errors.py
# 作用: 定义 ConversationStore 领域错误 DTO、异常对象和错误码默认重试策略。
# 边界: 仅封装 ConversationStore 稳定错误语义，不暴露数据库、事件总线或业务组件原始异常。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.conversation_store.dto import ConversationStoreDto, JsonMap
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[ConversationErrorCode, bool]] = {
    ConversationErrorCode.SESSION_NOT_FOUND: False,
    ConversationErrorCode.SESSION_CLOSED: False,
    ConversationErrorCode.SESSION_ARCHIVED: False,
    ConversationErrorCode.SESSION_PET_CONFLICT: False,
    ConversationErrorCode.SESSION_USER_CONFLICT: False,
    ConversationErrorCode.MESSAGE_NOT_FOUND: False,
    ConversationErrorCode.MESSAGE_DUPLICATE: False,
    ConversationErrorCode.MESSAGE_APPEND_FAILED: True,
    ConversationErrorCode.MESSAGE_ALREADY_FINALIZED: False,
    ConversationErrorCode.MESSAGE_INVALID_STATE: False,
    ConversationErrorCode.MESSAGE_TOO_LARGE: False,
    ConversationErrorCode.METADATA_TOO_LARGE: False,
    ConversationErrorCode.ATTACHMENT_LIMIT_EXCEEDED: False,
    ConversationErrorCode.STORE_UNAVAILABLE: True,
    ConversationErrorCode.OPERATION_TIMEOUT: True,
    ConversationErrorCode.INVALID_ARGUMENT: False,
}


class ConversationStoreErrorDto(ConversationStoreDto):
    """ConversationStore 统一错误 DTO。"""

    code: ConversationErrorCode = Field(
        description="ConversationStore 稳定错误码。",
    )
    operation: ConversationOperation = Field(
        description="发生错误的 ConversationStore 操作名。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明；不作为最终用户文案。",
    )
    retryable: bool = Field(
        description="调用方是否可以在重新加载状态或稍后等待后重试。",
    )
    request_id: str | None = Field(
        default=None,
        min_length=1,
        description="本次请求 ID；调用方未提供时为空。",
    )
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        description="本次全链路追踪 ID；调用方未提供时为空。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="冲突对象摘要，例如既有 session 锚点、消息状态或分页参数。",
    )


def is_conversation_error_retryable_by_default(
    code: ConversationErrorCode,
) -> bool:
    """判断指定 ConversationStore 错误码默认是否可重试。

    :param code: ConversationStore 稳定错误码。
    :return: 若该错误码默认允许调用方重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_conversation_store_error_dto(
    *,
    code: ConversationErrorCode,
    operation: ConversationOperation,
    message: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> ConversationStoreErrorDto:
    """构建 ConversationStore 统一错误 DTO。

    :param code: ConversationStore 稳定错误码。
    :param operation: 发生错误的 ConversationStore 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param conflict_with: 冲突对象摘要。
    :return: 已按默认重试策略补齐的 ConversationStore 错误 DTO。
    """

    resolved_retryable = (
        is_conversation_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return ConversationStoreErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        conflict_with=conflict_with,
    )


class ConversationStoreError(Exception):
    """ConversationStore 领域异常。"""

    def __init__(
        self,
        *,
        code: ConversationErrorCode,
        operation: ConversationOperation,
        message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 ConversationStore 领域异常。

        :param code: ConversationStore 稳定错误码。
        :param operation: 发生错误的 ConversationStore 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param conflict_with: 冲突对象摘要。
        :return: None。
        """

        self.error = build_conversation_store_error_dto(
            code=code,
            operation=operation,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            retryable=retryable,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> ConversationErrorCode:
        """读取 ConversationStore 稳定错误码。

        :return: 当前异常对应的 ConversationStore 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> ConversationOperation:
        """读取发生错误的 ConversationStore 操作名。

        :return: 当前异常对应的 ConversationStore 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> ConversationStoreErrorDto:
        """转换为 ConversationStore 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "ConversationStoreError",
    "ConversationStoreErrorDto",
    "build_conversation_store_error_dto",
    "is_conversation_error_retryable_by_default",
)
