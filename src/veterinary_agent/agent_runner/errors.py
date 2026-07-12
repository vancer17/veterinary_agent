##################################################################################################
# 文件: src/veterinary_agent/agent_runner/errors.py
# 作用: 定义 AgentRunner 统一领域异常、错误 DTO 构造函数与默认重试策略。
# 边界: 仅封装稳定错误语义，不暴露模型原文、prompt 正文、供应商 SDK 异常或敏感上下文。
##################################################################################################

from typing import Final

from veterinary_agent.agent_runner.dto import AgentRunnerErrorDto, JsonMap
from veterinary_agent.agent_runner.enums import (
    AgentRunnerErrorCode,
    AgentRunnerOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[AgentRunnerErrorCode, bool]] = {
    AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY: True,
    AgentRunnerErrorCode.AGENT_SPEC_NOT_FOUND: False,
    AgentRunnerErrorCode.AGENT_SPEC_VERSION_UNAVAILABLE: False,
    AgentRunnerErrorCode.AGENT_RUN_REQUEST_INVALID: False,
    AgentRunnerErrorCode.PROMPT_RENDER_FAILED: False,
    AgentRunnerErrorCode.TOKEN_BUDGET_EXCEEDED: False,
    AgentRunnerErrorCode.MODEL_TIMEOUT: True,
    AgentRunnerErrorCode.MODEL_PROVIDER_ERROR: True,
    AgentRunnerErrorCode.TOOL_PERMISSION_DENIED: False,
    AgentRunnerErrorCode.TOOL_EXECUTION_FAILED: True,
    AgentRunnerErrorCode.OUTPUT_PARSE_FAILED: False,
    AgentRunnerErrorCode.OUTPUT_SCHEMA_VALIDATION_FAILED: False,
    AgentRunnerErrorCode.AGENT_RETRY_EXHAUSTED: True,
    AgentRunnerErrorCode.AGENT_CANCELLED: False,
}


def is_agent_runner_error_retryable_by_default(
    code: AgentRunnerErrorCode,
) -> bool:
    """判断指定 AgentRunner 错误码默认是否可重试。

    :param code: AgentRunner 稳定错误码。
    :return: 若错误默认允许稍后重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_agent_runner_error_dto(
    *,
    code: AgentRunnerErrorCode,
    operation: AgentRunnerOperation,
    message: str,
    retryable: bool | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    agent_id: str | None = None,
    agent_version: str | None = None,
    model_profile_id: str | None = None,
    conflict_with: JsonMap | None = None,
) -> AgentRunnerErrorDto:
    """构建 AgentRunner 统一错误 DTO。

    :param code: AgentRunner 稳定错误码。
    :param operation: 发生错误的 AgentRunner 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param run_id: 可选 Agent 运行 ID。
    :param request_id: 可选入口请求 ID。
    :param trace_id: 可选全链路追踪 ID。
    :param agent_id: 可选 Agent ID。
    :param agent_version: 可选 Agent 版本。
    :param model_profile_id: 可选模型 profile ID。
    :param conflict_with: 可选脱敏冲突摘要。
    :return: 已补齐默认重试策略的 AgentRunner 错误 DTO。
    """

    resolved_retryable = (
        is_agent_runner_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return AgentRunnerErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        run_id=run_id,
        request_id=request_id,
        trace_id=trace_id,
        agent_id=agent_id,
        agent_version=agent_version,
        model_profile_id=model_profile_id,
        conflict_with=conflict_with,
    )


class AgentRunnerError(Exception):
    """AgentRunner 领域异常。"""

    def __init__(
        self,
        *,
        code: AgentRunnerErrorCode,
        operation: AgentRunnerOperation,
        message: str,
        retryable: bool | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        model_profile_id: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 AgentRunner 领域异常。

        :param code: AgentRunner 稳定错误码。
        :param operation: 发生错误的 AgentRunner 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param run_id: 可选 Agent 运行 ID。
        :param request_id: 可选入口请求 ID。
        :param trace_id: 可选全链路追踪 ID。
        :param agent_id: 可选 Agent ID。
        :param agent_version: 可选 Agent 版本。
        :param model_profile_id: 可选模型 profile ID。
        :param conflict_with: 可选脱敏冲突摘要。
        :return: None。
        """

        self.error = build_agent_runner_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            run_id=run_id,
            request_id=request_id,
            trace_id=trace_id,
            agent_id=agent_id,
            agent_version=agent_version,
            model_profile_id=model_profile_id,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> AgentRunnerErrorCode:
        """读取 AgentRunner 稳定错误码。

        :return: 当前异常对应的 AgentRunner 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> AgentRunnerOperation:
        """读取发生错误的 AgentRunner 操作名。

        :return: 当前异常对应的 AgentRunner 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若错误允许调用方重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> AgentRunnerErrorDto:
        """转换为 AgentRunner 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def with_context(
        self,
        *,
        run_id: str,
        request_id: str,
        trace_id: str,
        agent_id: str,
        agent_version: str,
        model_profile_id: str | None = None,
    ) -> "AgentRunnerError":
        """补齐运行上下文并返回新的领域异常。

        :param run_id: Agent 运行 ID。
        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param agent_id: Agent ID。
        :param agent_version: Agent 版本。
        :param model_profile_id: 可选模型 profile ID。
        :return: 保留原始错误语义并补齐上下文的新异常。
        """

        return AgentRunnerError(
            code=self.error.code,
            operation=self.error.operation,
            message=self.error.message,
            retryable=self.error.retryable,
            run_id=self.error.run_id or run_id,
            request_id=self.error.request_id or request_id,
            trace_id=self.error.trace_id or trace_id,
            agent_id=self.error.agent_id or agent_id,
            agent_version=self.error.agent_version or agent_version,
            model_profile_id=self.error.model_profile_id or model_profile_id,
            conflict_with=self.error.conflict_with,
        )

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "AgentRunnerError",
    "build_agent_runner_error_dto",
    "is_agent_runner_error_retryable_by_default",
)
