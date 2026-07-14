##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/errors.py
# 作用: 定义 GuardrailFramework 统一错误 DTO、领域异常和默认可重试判定。
# 边界: 仅规范错误表达，不映射 HTTP 状态码、不执行 handler、不写入 trace 或指标。
##################################################################################################

from pydantic import Field

from veterinary_agent.guardrail_framework.dto import (
    GuardrailFrameworkDto,
    JsonMap,
)
from veterinary_agent.guardrail_framework.enums import (
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
)

_DEFAULT_RETRYABLE_CODES: frozenset[GuardrailFrameworkErrorCode] = frozenset(
    {
        GuardrailFrameworkErrorCode.GUARDRAIL_NOT_READY,
        GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_TIMEOUT,
        GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_RETRY_EXHAUSTED,
        GuardrailFrameworkErrorCode.GUARDRAIL_TRACE_DEGRADED,
        GuardrailFrameworkErrorCode.GUARDRAIL_RUNTIME_CONFIG_UNAVAILABLE,
        GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
    }
)


class GuardrailFrameworkErrorDto(GuardrailFrameworkDto):
    """GuardrailFramework 统一错误 DTO。"""

    code: GuardrailFrameworkErrorCode = Field(description="稳定错误码。")
    operation: GuardrailFrameworkOperation = Field(description="发生错误的操作名。")
    message: str = Field(min_length=1, description="面向工程排障的错误说明。")
    retryable: bool = Field(description="调用方是否可以稍后重试。")
    stage: GuardrailStage | None = Field(default=None, description="护栏阶段。")
    request_id: str | None = Field(default=None, min_length=1, description="请求 ID。")
    trace_id: str | None = Field(default=None, min_length=1, description="trace ID。")
    run_id: str | None = Field(default=None, min_length=1, description="图运行 ID。")
    task_id: str | None = Field(default=None, min_length=1, description="任务 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="segment ID。",
    )
    policy_id: str | None = Field(
        default=None,
        min_length=1,
        description="关联策略 ID。",
    )
    handler_ref: str | None = Field(
        default=None,
        min_length=1,
        description="关联 handler 引用。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="不含敏感正文的冲突摘要。",
    )


def is_guardrail_framework_error_retryable_by_default(
    code: GuardrailFrameworkErrorCode,
) -> bool:
    """判断 GuardrailFramework 错误码是否默认可重试。

    :param code: 待判断的稳定错误码。
    :return: 若错误默认允许稍后重试则返回 True。
    """

    return code in _DEFAULT_RETRYABLE_CODES


def build_guardrail_framework_error_dto(
    *,
    code: GuardrailFrameworkErrorCode,
    operation: GuardrailFrameworkOperation,
    message: str,
    retryable: bool | None = None,
    stage: GuardrailStage | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    segment_id: str | None = None,
    policy_id: str | None = None,
    handler_ref: str | None = None,
    conflict_with: JsonMap | None = None,
) -> GuardrailFrameworkErrorDto:
    """构建 GuardrailFramework 统一错误 DTO。

    :param code: 稳定错误码。
    :param operation: 发生错误的操作名。
    :param message: 面向工程排障的错误说明。
    :param retryable: 可选重试标记；未传入时按错误码默认值解析。
    :param stage: 可选护栏阶段。
    :param request_id: 可选请求 ID。
    :param trace_id: 可选 trace ID。
    :param run_id: 可选图运行 ID。
    :param task_id: 可选任务 ID。
    :param segment_id: 可选 segment ID。
    :param policy_id: 可选策略 ID。
    :param handler_ref: 可选 handler 引用。
    :param conflict_with: 可选冲突摘要。
    :return: GuardrailFramework 错误 DTO。
    """

    return GuardrailFrameworkErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=(
            is_guardrail_framework_error_retryable_by_default(code)
            if retryable is None
            else retryable
        ),
        stage=stage,
        request_id=request_id,
        trace_id=trace_id,
        run_id=run_id,
        task_id=task_id,
        segment_id=segment_id,
        policy_id=policy_id,
        handler_ref=handler_ref,
        conflict_with=conflict_with,
    )


class GuardrailFrameworkError(Exception):
    """携带稳定错误 DTO 的 GuardrailFramework 领域异常。"""

    def __init__(
        self,
        *,
        code: GuardrailFrameworkErrorCode,
        operation: GuardrailFrameworkOperation,
        message: str,
        retryable: bool | None = None,
        stage: GuardrailStage | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        segment_id: str | None = None,
        policy_id: str | None = None,
        handler_ref: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 GuardrailFramework 领域异常。

        :param code: 稳定错误码。
        :param operation: 发生错误的操作名。
        :param message: 面向工程排障的错误说明。
        :param retryable: 可选重试标记。
        :param stage: 可选护栏阶段。
        :param request_id: 可选请求 ID。
        :param trace_id: 可选 trace ID。
        :param run_id: 可选图运行 ID。
        :param task_id: 可选任务 ID。
        :param segment_id: 可选 segment ID。
        :param policy_id: 可选策略 ID。
        :param handler_ref: 可选 handler 引用。
        :param conflict_with: 可选冲突摘要。
        :return: None。
        """

        self.error = build_guardrail_framework_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            stage=stage,
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            segment_id=segment_id,
            policy_id=policy_id,
            handler_ref=handler_ref,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> GuardrailFrameworkErrorCode:
        """读取稳定错误码。

        :return: 当前异常携带的 GuardrailFramework 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> GuardrailFrameworkOperation:
        """读取发生错误的操作名。

        :return: 当前异常对应的 GuardrailFramework 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可稍后重试则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> GuardrailFrameworkErrorDto:
        """转换为统一错误 DTO。

        :return: 当前异常携带的 GuardrailFramework 错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.operation.value}:{self.code.value}:{self.error.message}"


__all__: tuple[str, ...] = (
    "GuardrailFrameworkError",
    "GuardrailFrameworkErrorDto",
    "build_guardrail_framework_error_dto",
    "is_guardrail_framework_error_retryable_by_default",
)
