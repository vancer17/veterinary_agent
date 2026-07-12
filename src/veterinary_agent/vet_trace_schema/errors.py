##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/errors.py
# 作用: 定义 VetTraceSchema 统一错误 DTO、领域异常与默认重试策略。
# 边界: 仅封装 L2 业务 trace schema 错误语义，不暴露 jsonschema 内部异常或上游业务原文。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.vet_trace_schema.dto import JsonMap, VetTraceSchemaDto
from veterinary_agent.vet_trace_schema.enums import (
    VetTraceErrorCode,
    VetTraceOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[VetTraceErrorCode, bool]] = {
    VetTraceErrorCode.VET_TRACE_SCHEMA_VERSION_NOT_FOUND: False,
    VetTraceErrorCode.VET_TRACE_CAPTURE_POLICY_NOT_FOUND: False,
    VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID: False,
    VetTraceErrorCode.VET_TRACE_PATCH_PAYLOAD_INVALID: False,
    VetTraceErrorCode.VET_TRACE_PET_CONFLICT: False,
    VetTraceErrorCode.VET_TRACE_AUDIT_TIER_CONFLICT: False,
    VetTraceErrorCode.VET_TRACE_REQUIRED_ARTIFACT_MISSING: False,
    VetTraceErrorCode.VET_TRACE_REASONING_DISPLAY_UNSAFE: False,
    VetTraceErrorCode.VET_TRACE_GUARD_CHAIN_INCOMPLETE: False,
    VetTraceErrorCode.VET_TRACE_SEGMENT_INCONSISTENT: False,
    VetTraceErrorCode.VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE: True,
    VetTraceErrorCode.VET_TRACE_PROJECTION_BUILD_FAILED: True,
    VetTraceErrorCode.VET_TRACE_INVALID_ARGUMENT: False,
}


class VetTraceSchemaErrorDto(VetTraceSchemaDto):
    """VetTraceSchema 统一错误 DTO。"""

    code: VetTraceErrorCode = Field(description="VetTraceSchema 稳定错误码。")
    operation: VetTraceOperation = Field(
        description="发生错误的 VetTraceSchema 操作名。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明。",
    )
    retryable: bool = Field(description="调用方是否可以稍后重试。")
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


def is_vet_trace_error_retryable_by_default(code: VetTraceErrorCode) -> bool:
    """判断指定 VetTraceSchema 错误码默认是否可重试。

    :param code: VetTraceSchema 稳定错误码。
    :return: 若该错误码默认允许调用方重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_vet_trace_schema_error_dto(
    *,
    code: VetTraceErrorCode,
    operation: VetTraceOperation,
    message: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> VetTraceSchemaErrorDto:
    """构建 VetTraceSchema 统一错误 DTO。

    :param code: VetTraceSchema 稳定错误码。
    :param operation: 发生错误的 VetTraceSchema 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次逻辑链 ID。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param conflict_with: 冲突对象摘要。
    :return: 已按默认重试策略补齐的 VetTraceSchema 错误 DTO。
    """

    resolved_retryable = (
        is_vet_trace_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return VetTraceSchemaErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        conflict_with=conflict_with,
    )


class VetTraceSchemaError(Exception):
    """VetTraceSchema 领域异常。"""

    def __init__(
        self,
        *,
        code: VetTraceErrorCode,
        operation: VetTraceOperation,
        message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        retryable: bool | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 VetTraceSchema 领域异常。

        :param code: VetTraceSchema 稳定错误码。
        :param operation: 发生错误的 VetTraceSchema 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param conflict_with: 冲突对象摘要。
        :return: None。
        """

        self.error = build_vet_trace_schema_error_dto(
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
    def code(self) -> VetTraceErrorCode:
        """读取 VetTraceSchema 稳定错误码。

        :return: 当前异常对应的 VetTraceSchema 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> VetTraceOperation:
        """读取发生错误的 VetTraceSchema 操作名。

        :return: 当前异常对应的 VetTraceSchema 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> VetTraceSchemaErrorDto:
        """转换为 VetTraceSchema 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "VetTraceSchemaError",
    "VetTraceSchemaErrorDto",
    "build_vet_trace_schema_error_dto",
    "is_vet_trace_error_retryable_by_default",
)
