##################################################################################################
# 文件: src/veterinary_agent/education_agent/errors.py
# 作用: 定义 EducationAgent 统一错误 DTO、领域异常和默认可重试判定。
# 边界: 仅规范错误表达，不执行科普生成、不映射 HTTP 状态码、不调用下游组件。
##################################################################################################

from pydantic import Field

from veterinary_agent.education_agent.dto import EducationAgentDto, JsonMap
from veterinary_agent.education_agent.enums import (
    EducationAgentErrorCode,
    EducationAgentOperation,
)

_DEFAULT_RETRYABLE_CODES: frozenset[EducationAgentErrorCode] = frozenset(
    {
        EducationAgentErrorCode.EDUCATION_NOT_READY,
        EducationAgentErrorCode.EDUCATION_PLAN_FAILED,
        EducationAgentErrorCode.EDUCATION_RAG_DEGRADED,
        EducationAgentErrorCode.EDUCATION_WRITER_TIMEOUT,
        EducationAgentErrorCode.EDUCATION_GROUNDING_CHECK_FAILED,
        EducationAgentErrorCode.EDUCATION_TOKEN_BUDGET_EXCEEDED,
        EducationAgentErrorCode.EDUCATION_RUNTIME_CONFIG_UNAVAILABLE,
        EducationAgentErrorCode.EDUCATION_INTERNAL_ERROR,
    }
)


class EducationAgentErrorDto(EducationAgentDto):
    """EducationAgent 统一错误 DTO。"""

    code: EducationAgentErrorCode = Field(description="稳定错误码。")
    operation: EducationAgentOperation = Field(description="发生错误的操作名。")
    message: str = Field(min_length=1, description="面向工程排障的错误说明。")
    retryable: bool = Field(description="调用方是否可以稍后重试。")
    request_id: str | None = Field(default=None, min_length=1, description="请求 ID。")
    trace_id: str | None = Field(default=None, min_length=1, description="trace ID。")
    task_id: str | None = Field(default=None, min_length=1, description="子任务 ID。")
    conflict_with: JsonMap | None = Field(
        default=None,
        description="不含用户正文的冲突摘要。",
    )


def is_education_agent_error_retryable_by_default(
    code: EducationAgentErrorCode,
) -> bool:
    """判断科普错误码是否默认可重试。

    :param code: 待判断的稳定错误码。
    :return: 若错误默认允许稍后重试则返回 True。
    """

    return code in _DEFAULT_RETRYABLE_CODES


def build_education_agent_error_dto(
    *,
    code: EducationAgentErrorCode,
    operation: EducationAgentOperation,
    message: str,
    retryable: bool | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    task_id: str | None = None,
    conflict_with: JsonMap | None = None,
) -> EducationAgentErrorDto:
    """构建 EducationAgent 统一错误 DTO。

    :param code: 稳定错误码。
    :param operation: 发生错误的操作名。
    :param message: 面向工程排障的错误说明。
    :param retryable: 可选重试标记；未传入时按错误码默认值解析。
    :param request_id: 可选请求 ID。
    :param trace_id: 可选 trace ID。
    :param task_id: 可选子任务 ID。
    :param conflict_with: 可选冲突摘要。
    :return: 科普错误 DTO。
    """

    return EducationAgentErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=(
            is_education_agent_error_retryable_by_default(code)
            if retryable is None
            else retryable
        ),
        request_id=request_id,
        trace_id=trace_id,
        task_id=task_id,
        conflict_with=conflict_with,
    )


class EducationAgentError(Exception):
    """携带稳定错误 DTO 的 EducationAgent 领域异常。"""

    def __init__(
        self,
        *,
        code: EducationAgentErrorCode,
        operation: EducationAgentOperation,
        message: str,
        retryable: bool | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 EducationAgent 领域异常。

        :param code: 稳定错误码。
        :param operation: 发生错误的操作名。
        :param message: 面向工程排障的错误说明。
        :param retryable: 可选重试标记。
        :param request_id: 可选请求 ID。
        :param trace_id: 可选 trace ID。
        :param task_id: 可选子任务 ID。
        :param conflict_with: 可选冲突摘要。
        :return: None。
        """

        self.error = build_education_agent_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            request_id=request_id,
            trace_id=trace_id,
            task_id=task_id,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> EducationAgentErrorCode:
        """读取稳定错误码。

        :return: 当前异常携带的科普错误码。
        """

        return self.error.code

    @property
    def operation(self) -> EducationAgentOperation:
        """读取发生错误的操作名。

        :return: 当前异常对应的科普操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可稍后重试则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> EducationAgentErrorDto:
        """转换为统一错误 DTO。

        :return: 当前异常携带的科普错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.operation.value}:{self.code.value}:{self.error.message}"


__all__: tuple[str, ...] = (
    "EducationAgentError",
    "EducationAgentErrorDto",
    "build_education_agent_error_dto",
    "is_education_agent_error_retryable_by_default",
)
