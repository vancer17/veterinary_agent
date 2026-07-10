##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/errors.py
# 作用: 定义 AgentApplicationService 统一错误 DTO、领域异常与默认重试策略。
# 边界: 仅封装应用编排层稳定错误语义，不执行 HTTP 映射、不泄露下游异常对象或敏感业务正文。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.agent_application_service.dto import (
    AgentApplicationDto,
    JsonMap,
)
from veterinary_agent.agent_application_service.enums import (
    AgentApplicationErrorCode,
    AgentApplicationOperation,
    AgentApplicationPhase,
    AgentTraceDeliveryStatus,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[AgentApplicationErrorCode, bool]] = {
    AgentApplicationErrorCode.APPLICATION_NOT_READY: True,
    AgentApplicationErrorCode.REQUIRED_CONTEXT_MISSING: False,
    AgentApplicationErrorCode.PET_SESSION_CONFLICT: False,
    AgentApplicationErrorCode.SESSION_IDENTITY_CONFLICT: False,
    AgentApplicationErrorCode.SESSION_CLOSED: False,
    AgentApplicationErrorCode.SESSION_ARCHIVED: False,
    AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE: True,
    AgentApplicationErrorCode.TRACE_START_FAILED: True,
    AgentApplicationErrorCode.USER_MESSAGE_PERSIST_FAILED: True,
    AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE: True,
    AgentApplicationErrorCode.GRAPH_EXECUTION_TIMEOUT: True,
    AgentApplicationErrorCode.GRAPH_EXECUTION_FAILED: True,
    AgentApplicationErrorCode.GRAPH_RESULT_INVALID: False,
    AgentApplicationErrorCode.TURN_ALREADY_RUNNING: True,
    AgentApplicationErrorCode.TURN_CANCELLED: False,
    AgentApplicationErrorCode.INTERNAL_ERROR: True,
}


class AgentApplicationErrorDto(AgentApplicationDto):
    """AgentApplicationService 统一错误 DTO。"""

    code: AgentApplicationErrorCode = Field(description="应用服务稳定错误码。")
    operation: AgentApplicationOperation = Field(description="发生错误的应用操作。")
    phase: AgentApplicationPhase = Field(description="发生错误的应用编排阶段。")
    message: str = Field(min_length=1, description="面向工程排障的错误说明。")
    retryable: bool = Field(description="调用方是否可以重试。")
    request_id: str = Field(min_length=1, description="本次请求 ID。")
    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    turn_id: str | None = Field(
        default=None, min_length=1, description="稳定 turn ID。"
    )
    run_id: str | None = Field(
        default=None, min_length=1, description="稳定图运行 ID。"
    )
    dependency: str | None = Field(
        default=None,
        min_length=1,
        description="产生错误的下游组件名。",
    )
    dependency_error_code: str | None = Field(
        default=None,
        min_length=1,
        description="下游组件稳定错误码。",
    )
    trace_delivery_status: AgentTraceDeliveryStatus = Field(
        description="当前逻辑链交付状态。",
    )
    details: JsonMap = Field(default_factory=dict, description="安全错误摘要。")


def is_agent_application_error_retryable_by_default(
    code: AgentApplicationErrorCode,
) -> bool:
    """判断指定应用错误码默认是否可重试。

    :param code: AgentApplicationService 稳定错误码。
    :return: 若该错误码默认允许重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_agent_application_error_dto(
    *,
    code: AgentApplicationErrorCode,
    operation: AgentApplicationOperation,
    phase: AgentApplicationPhase,
    message: str,
    request_id: str,
    trace_id: str,
    turn_id: str | None = None,
    run_id: str | None = None,
    dependency: str | None = None,
    dependency_error_code: str | None = None,
    trace_delivery_status: AgentTraceDeliveryStatus = AgentTraceDeliveryStatus.DEGRADED,
    retryable: bool | None = None,
    details: JsonMap | None = None,
) -> AgentApplicationErrorDto:
    """构建 AgentApplicationService 统一错误 DTO。

    :param code: 应用服务稳定错误码。
    :param operation: 发生错误的应用操作。
    :param phase: 发生错误的应用编排阶段。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次逻辑链 ID。
    :param turn_id: 可选稳定 turn ID。
    :param run_id: 可选稳定图运行 ID。
    :param dependency: 可选下游组件名。
    :param dependency_error_code: 可选下游稳定错误码。
    :param trace_delivery_status: 当前逻辑链交付状态。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param details: 安全错误摘要。
    :return: 已补齐默认重试策略的统一错误 DTO。
    """

    resolved_retryable = (
        is_agent_application_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return AgentApplicationErrorDto(
        code=code,
        operation=operation,
        phase=phase,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        turn_id=turn_id,
        run_id=run_id,
        dependency=dependency,
        dependency_error_code=dependency_error_code,
        trace_delivery_status=trace_delivery_status,
        details=details or {},
    )


class AgentApplicationServiceError(Exception):
    """AgentApplicationService 领域异常。"""

    def __init__(
        self,
        *,
        code: AgentApplicationErrorCode,
        operation: AgentApplicationOperation,
        phase: AgentApplicationPhase,
        message: str,
        request_id: str,
        trace_id: str,
        turn_id: str | None = None,
        run_id: str | None = None,
        dependency: str | None = None,
        dependency_error_code: str | None = None,
        trace_delivery_status: AgentTraceDeliveryStatus = (
            AgentTraceDeliveryStatus.DEGRADED
        ),
        retryable: bool | None = None,
        details: JsonMap | None = None,
    ) -> None:
        """初始化 AgentApplicationService 领域异常。

        :param code: 应用服务稳定错误码。
        :param operation: 发生错误的应用操作。
        :param phase: 发生错误的应用编排阶段。
        :param message: 面向工程排障的错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param turn_id: 可选稳定 turn ID。
        :param run_id: 可选稳定图运行 ID。
        :param dependency: 可选下游组件名。
        :param dependency_error_code: 可选下游稳定错误码。
        :param trace_delivery_status: 当前逻辑链交付状态。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param details: 安全错误摘要。
        :return: None。
        """

        self.error = build_agent_application_error_dto(
            code=code,
            operation=operation,
            phase=phase,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            turn_id=turn_id,
            run_id=run_id,
            dependency=dependency,
            dependency_error_code=dependency_error_code,
            trace_delivery_status=trace_delivery_status,
            retryable=retryable,
            details=details,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> AgentApplicationErrorCode:
        """读取当前应用错误码。

        :return: 当前异常对应的稳定错误码。
        """

        return self.error.code

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若当前错误允许重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> AgentApplicationErrorDto:
        """转换为统一错误 DTO。

        :return: 当前异常携带的 AgentApplicationService 错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作、阶段、错误码和说明的字符串。
        """

        return (
            f"{self.error.operation}:{self.error.phase}:"
            f"{self.error.code}:{self.error.message}"
        )


__all__: tuple[str, ...] = (
    "AgentApplicationErrorDto",
    "AgentApplicationServiceError",
    "build_agent_application_error_dto",
    "is_agent_application_error_retryable_by_default",
)
