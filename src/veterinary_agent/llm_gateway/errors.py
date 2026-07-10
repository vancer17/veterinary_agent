##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/errors.py
# 作用: 定义 LlmGateway 统一领域异常、错误 DTO 构造函数与默认重试策略。
# 边界: 仅封装稳定错误语义，不暴露代理响应正文、供应商 SDK 异常或敏感配置。
##################################################################################################

from typing import Final

from veterinary_agent.llm_gateway.dto import JsonMap, LlmErrorDto
from veterinary_agent.llm_gateway.enums import (
    LlmGatewayErrorCode,
    LlmGatewayOperation,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[LlmGatewayErrorCode, bool]] = {
    LlmGatewayErrorCode.LLM_GATEWAY_NOT_READY: True,
    LlmGatewayErrorCode.LLM_PROFILE_NOT_FOUND: False,
    LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE: True,
    LlmGatewayErrorCode.LLM_CAPABILITY_MISMATCH: False,
    LlmGatewayErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED: False,
    LlmGatewayErrorCode.LLM_TIMEOUT: True,
    LlmGatewayErrorCode.LLM_FIRST_TOKEN_TIMEOUT: True,
    LlmGatewayErrorCode.LLM_PROXY_UNAVAILABLE: True,
    LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE: True,
    LlmGatewayErrorCode.LLM_RATE_LIMITED: True,
    LlmGatewayErrorCode.LLM_INVALID_REQUEST: False,
    LlmGatewayErrorCode.LLM_SAFETY_BLOCKED: False,
    LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE: False,
    LlmGatewayErrorCode.LLM_RETRY_EXHAUSTED: True,
    LlmGatewayErrorCode.LLM_CONCURRENCY_LIMITED: True,
    LlmGatewayErrorCode.LLM_CANCELLED: False,
}


def is_llm_gateway_error_retryable_by_default(
    code: LlmGatewayErrorCode,
) -> bool:
    """判断指定 LlmGateway 错误码默认是否可重试。

    :param code: LlmGateway 稳定错误码。
    :return: 若错误默认允许稍后重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_llm_gateway_error_dto(
    *,
    code: LlmGatewayErrorCode,
    operation: LlmGatewayOperation,
    message: str,
    retryable: bool | None = None,
    call_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    model_profile_id: str | None = None,
    provider_route_id: str | None = None,
    conflict_with: JsonMap | None = None,
) -> LlmErrorDto:
    """构建 LlmGateway 统一错误 DTO。

    :param code: LlmGateway 稳定错误码。
    :param operation: 发生错误的 LlmGateway 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param call_id: 可选逻辑模型调用 ID。
    :param request_id: 可选入口请求 ID。
    :param trace_id: 可选全链路追踪 ID。
    :param model_profile_id: 可选模型 profile ID。
    :param provider_route_id: 可选供应商路由 ID。
    :param conflict_with: 可选安全错误详情。
    :return: 已补齐默认重试策略的 LlmGateway 错误 DTO。
    """

    resolved_retryable = (
        is_llm_gateway_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return LlmErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=resolved_retryable,
        call_id=call_id,
        request_id=request_id,
        trace_id=trace_id,
        model_profile_id=model_profile_id,
        provider_route_id=provider_route_id,
        conflict_with=conflict_with,
    )


class LlmGatewayError(Exception):
    """LlmGateway 领域异常。"""

    def __init__(
        self,
        *,
        code: LlmGatewayErrorCode,
        operation: LlmGatewayOperation,
        message: str,
        retryable: bool | None = None,
        call_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        model_profile_id: str | None = None,
        provider_route_id: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 LlmGateway 领域异常。

        :param code: LlmGateway 稳定错误码。
        :param operation: 发生错误的 LlmGateway 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param call_id: 可选逻辑模型调用 ID。
        :param request_id: 可选入口请求 ID。
        :param trace_id: 可选全链路追踪 ID。
        :param model_profile_id: 可选模型 profile ID。
        :param provider_route_id: 可选供应商路由 ID。
        :param conflict_with: 可选安全错误详情。
        :return: None。
        """

        self.error = build_llm_gateway_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            call_id=call_id,
            request_id=request_id,
            trace_id=trace_id,
            model_profile_id=model_profile_id,
            provider_route_id=provider_route_id,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> LlmGatewayErrorCode:
        """读取 LlmGateway 稳定错误码。

        :return: 当前异常对应的 LlmGateway 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> LlmGatewayOperation:
        """读取发生错误的 LlmGateway 操作名。

        :return: 当前异常对应的 LlmGateway 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若错误允许调用方重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> LlmErrorDto:
        """转换为 LlmGateway 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def with_context(
        self,
        *,
        call_id: str,
        request_id: str,
        trace_id: str,
        model_profile_id: str | None = None,
        provider_route_id: str | None = None,
    ) -> "LlmGatewayError":
        """补齐调用上下文并返回新的领域异常。

        :param call_id: 逻辑模型调用 ID。
        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param model_profile_id: 可选模型 profile ID。
        :param provider_route_id: 可选供应商路由 ID。
        :return: 保留原始错误语义并补齐上下文的新异常。
        """

        return LlmGatewayError(
            code=self.error.code,
            operation=self.error.operation,
            message=self.error.message,
            retryable=self.error.retryable,
            call_id=self.error.call_id or call_id,
            request_id=self.error.request_id or request_id,
            trace_id=self.error.trace_id or trace_id,
            model_profile_id=self.error.model_profile_id or model_profile_id,
            provider_route_id=self.error.provider_route_id or provider_route_id,
            conflict_with=self.error.conflict_with,
        )

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "LlmGatewayError",
    "build_llm_gateway_error_dto",
    "is_llm_gateway_error_retryable_by_default",
)
