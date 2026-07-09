##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/errors.py
# 作用: 定义 CheckpointStore 领域错误 DTO、异常对象和错误码默认重试策略。
# 边界: 仅封装 CheckpointStore 稳定错误语义，不暴露数据库、LangGraph 或网络依赖的原始异常。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.checkpoint_store.dto import CheckpointStoreDto, JsonMap
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[CheckpointErrorCode, bool]] = {
    CheckpointErrorCode.CHECKPOINT_NOT_FOUND: False,
    CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND: False,
    CheckpointErrorCode.CHECKPOINT_LOCKED: True,
    CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT: True,
    CheckpointErrorCode.CHECKPOINT_PET_CONFLICT: False,
    CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE: False,
    CheckpointErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED: False,
    CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED: False,
    CheckpointErrorCode.CHECKPOINT_LOCK_OWNER_MISMATCH: False,
    CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE: True,
    CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT: True,
    CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT: False,
}


class CheckpointStoreErrorDto(CheckpointStoreDto):
    """CheckpointStore 统一错误 DTO。"""

    code: CheckpointErrorCode = Field(
        description="CheckpointStore 稳定错误码。",
    )
    operation: CheckpointOperation = Field(
        description="发生错误的 CheckpointStore 操作名。",
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
        description="冲突对象摘要，例如当前持锁 run、当前版本或既有 pet_id。",
    )


def is_checkpoint_error_retryable_by_default(code: CheckpointErrorCode) -> bool:
    """判断指定 CheckpointStore 错误码默认是否可重试。

    :param code: CheckpointStore 稳定错误码。
    :return: 若该错误码默认允许调用方重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_checkpoint_store_error_dto(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> CheckpointStoreErrorDto:
    """构建 CheckpointStore 统一错误 DTO。

    :param code: CheckpointStore 稳定错误码。
    :param operation: 发生错误的 CheckpointStore 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param conflict_with: 冲突对象摘要。
    :return: 已按默认重试策略补齐的 CheckpointStore 错误 DTO。
    """

    resolved_retryable = (
        is_checkpoint_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return CheckpointStoreErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        conflict_with=conflict_with,
    )


class CheckpointStoreError(Exception):
    """CheckpointStore 领域异常。"""

    def __init__(
        self,
        *,
        code: CheckpointErrorCode,
        operation: CheckpointOperation,
        message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 CheckpointStore 领域异常。

        :param code: CheckpointStore 稳定错误码。
        :param operation: 发生错误的 CheckpointStore 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param conflict_with: 冲突对象摘要。
        :return: None。
        """

        self.error = build_checkpoint_store_error_dto(
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
    def code(self) -> CheckpointErrorCode:
        """读取 CheckpointStore 稳定错误码。

        :return: 当前异常对应的 CheckpointStore 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> CheckpointOperation:
        """读取发生错误的 CheckpointStore 操作名。

        :return: 当前异常对应的 CheckpointStore 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> CheckpointStoreErrorDto:
        """转换为 CheckpointStore 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "CheckpointStoreError",
    "CheckpointStoreErrorDto",
    "build_checkpoint_store_error_dto",
    "is_checkpoint_error_retryable_by_default",
)
