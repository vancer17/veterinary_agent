##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/errors.py
# 作用: 定义 LogicTraceStore 统一错误 DTO、领域异常与默认重试策略。
# 边界: 仅封装逻辑链留痕稳定错误语义，不暴露数据库细节、业务 patch 原文或内部实现异常。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.logic_trace_store.dto import JsonMap, LogicTraceStoreDto
from veterinary_agent.logic_trace_store.enums import (
    LogicTraceErrorCode,
    LogicTraceOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[LogicTraceErrorCode, bool]] = {
    LogicTraceErrorCode.TRACE_NOT_FOUND: False,
    LogicTraceErrorCode.TRACE_ALREADY_FINALIZED: False,
    LogicTraceErrorCode.TRACE_EVENT_SCHEMA_INVALID: False,
    LogicTraceErrorCode.TRACE_CAPTURE_POLICY_NOT_FOUND: False,
    LogicTraceErrorCode.TRACE_ARTIFACT_UNAVAILABLE: True,
    LogicTraceErrorCode.TRACE_PROJECTION_BUILD_FAILED: True,
    LogicTraceErrorCode.TRACE_STORAGE_WRITE_FAILED: True,
    LogicTraceErrorCode.TRACE_OUTBOX_WRITE_FAILED: True,
    LogicTraceErrorCode.TRACE_STREAM_DELIVERY_FAILED: True,
    LogicTraceErrorCode.TRACE_OPERATION_TIMEOUT: True,
    LogicTraceErrorCode.TRACE_INVALID_ARGUMENT: False,
    LogicTraceErrorCode.TRACE_STORE_UNAVAILABLE: True,
}


class LogicTraceStoreErrorDto(LogicTraceStoreDto):
    """LogicTraceStore 统一错误 DTO。"""

    code: LogicTraceErrorCode = Field(description="LogicTraceStore 稳定错误码。")
    operation: LogicTraceOperation = Field(
        description="发生错误的 LogicTraceStore 操作名。",
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
        description="本次请求 ID。",
    )
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        description="本次逻辑链 ID。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="冲突对象摘要。",
    )


def is_logic_trace_error_retryable_by_default(code: LogicTraceErrorCode) -> bool:
    """判断指定 LogicTraceStore 错误码默认是否可重试。

    :param code: LogicTraceStore 稳定错误码。
    :return: 若该错误码默认允许调用方重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_logic_trace_store_error_dto(
    *,
    code: LogicTraceErrorCode,
    operation: LogicTraceOperation,
    message: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> LogicTraceStoreErrorDto:
    """构建 LogicTraceStore 统一错误 DTO。

    :param code: LogicTraceStore 稳定错误码。
    :param operation: 发生错误的 LogicTraceStore 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次逻辑链 ID。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param conflict_with: 冲突对象摘要。
    :return: 已按默认重试策略补齐的 LogicTraceStore 错误 DTO。
    """

    resolved_retryable = (
        is_logic_trace_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return LogicTraceStoreErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        conflict_with=conflict_with,
    )


class LogicTraceStoreError(Exception):
    """LogicTraceStore 领域异常。"""

    def __init__(
        self,
        *,
        code: LogicTraceErrorCode,
        operation: LogicTraceOperation,
        message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 LogicTraceStore 领域异常。

        :param code: LogicTraceStore 稳定错误码。
        :param operation: 发生错误的 LogicTraceStore 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param conflict_with: 冲突对象摘要。
        :return: None。
        """

        self.error = build_logic_trace_store_error_dto(
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
    def code(self) -> LogicTraceErrorCode:
        """读取 LogicTraceStore 稳定错误码。

        :return: 当前异常对应的 LogicTraceStore 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> LogicTraceOperation:
        """读取发生错误的 LogicTraceStore 操作名。

        :return: 当前异常对应的 LogicTraceStore 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> LogicTraceStoreErrorDto:
        """转换为 LogicTraceStore 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "LogicTraceStoreError",
    "LogicTraceStoreErrorDto",
    "build_logic_trace_store_error_dto",
    "is_logic_trace_error_retryable_by_default",
)
