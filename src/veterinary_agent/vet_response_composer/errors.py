##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/errors.py
# 作用: 定义 VetResponseComposer 统一错误 DTO、领域异常和默认可重试判定。
# 边界: 仅规范错误表达，不映射 HTTP 状态码、不调用存储、不执行回复发布。
##################################################################################################

from pydantic import Field

from veterinary_agent.vet_response_composer.dto import JsonMap, VetResponseComposerDto
from veterinary_agent.vet_response_composer.enums import (
    VetResponseComposerErrorCode,
    VetResponseComposerOperation,
)

_DEFAULT_RETRYABLE_CODES: frozenset[VetResponseComposerErrorCode] = frozenset(
    {
        VetResponseComposerErrorCode.COMPOSER_NOT_READY,
        VetResponseComposerErrorCode.COMPOSER_SAFETY_FIRST_LOCK_ACTIVE,
        VetResponseComposerErrorCode.COMPOSER_CONVERSATION_APPEND_FAILED,
        VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_READY_FAILED,
        VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_PUBLISHED_FAILED,
        VetResponseComposerErrorCode.COMPOSER_TRACE_DEGRADED,
        VetResponseComposerErrorCode.COMPOSER_RUNTIME_CONFIG_UNAVAILABLE,
        VetResponseComposerErrorCode.COMPOSER_INTERNAL_ERROR,
    }
)


class VetResponseComposerErrorDto(VetResponseComposerDto):
    """VetResponseComposer 统一错误 DTO。"""

    code: VetResponseComposerErrorCode = Field(description="稳定错误码。")
    operation: VetResponseComposerOperation = Field(description="发生错误的操作名。")
    message: str = Field(min_length=1, description="面向工程排障的错误说明。")
    retryable: bool = Field(description="调用方是否可以稍后重试。")
    request_id: str | None = Field(default=None, min_length=1, description="请求 ID。")
    trace_id: str | None = Field(default=None, min_length=1, description="trace ID。")
    run_id: str | None = Field(default=None, min_length=1, description="图运行 ID。")
    task_id: str | None = Field(default=None, min_length=1, description="任务 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="segment ID。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="不含用户正文的冲突摘要。",
    )


def is_vet_response_composer_error_retryable_by_default(
    code: VetResponseComposerErrorCode,
) -> bool:
    """判断 Composer 错误码是否默认可重试。

    :param code: 待判断的稳定错误码。
    :return: 若错误默认允许稍后重试则返回 True。
    """

    return code in _DEFAULT_RETRYABLE_CODES


def build_vet_response_composer_error_dto(
    *,
    code: VetResponseComposerErrorCode,
    operation: VetResponseComposerOperation,
    message: str,
    retryable: bool | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    segment_id: str | None = None,
    conflict_with: JsonMap | None = None,
) -> VetResponseComposerErrorDto:
    """构建 VetResponseComposer 统一错误 DTO。

    :param code: 稳定错误码。
    :param operation: 发生错误的操作名。
    :param message: 面向工程排障的错误说明。
    :param retryable: 可选重试标记；未传入时按错误码默认值解析。
    :param request_id: 可选请求 ID。
    :param trace_id: 可选 trace ID。
    :param run_id: 可选图运行 ID。
    :param task_id: 可选任务 ID。
    :param segment_id: 可选 segment ID。
    :param conflict_with: 可选冲突摘要。
    :return: Composer 错误 DTO。
    """

    return VetResponseComposerErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=(
            is_vet_response_composer_error_retryable_by_default(code)
            if retryable is None
            else retryable
        ),
        request_id=request_id,
        trace_id=trace_id,
        run_id=run_id,
        task_id=task_id,
        segment_id=segment_id,
        conflict_with=conflict_with,
    )


class VetResponseComposerError(Exception):
    """携带稳定错误 DTO 的 VetResponseComposer 领域异常。"""

    def __init__(
        self,
        *,
        code: VetResponseComposerErrorCode,
        operation: VetResponseComposerOperation,
        message: str,
        retryable: bool | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        segment_id: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 VetResponseComposer 领域异常。

        :param code: 稳定错误码。
        :param operation: 发生错误的操作名。
        :param message: 面向工程排障的错误说明。
        :param retryable: 可选重试标记。
        :param request_id: 可选请求 ID。
        :param trace_id: 可选 trace ID。
        :param run_id: 可选图运行 ID。
        :param task_id: 可选任务 ID。
        :param segment_id: 可选 segment ID。
        :param conflict_with: 可选冲突摘要。
        :return: None。
        """

        self.error = build_vet_response_composer_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            segment_id=segment_id,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> VetResponseComposerErrorCode:
        """读取稳定错误码。

        :return: 当前异常携带的 Composer 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> VetResponseComposerOperation:
        """读取发生错误的操作名。

        :return: 当前异常对应的 Composer 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可稍后重试则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> VetResponseComposerErrorDto:
        """转换为统一错误 DTO。

        :return: 当前异常携带的 Composer 错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.operation.value}:{self.code.value}:{self.error.message}"


__all__: tuple[str, ...] = (
    "VetResponseComposerError",
    "VetResponseComposerErrorDto",
    "build_vet_response_composer_error_dto",
    "is_vet_response_composer_error_retryable_by_default",
)
