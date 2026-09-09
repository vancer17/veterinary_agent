"""
LiteLLM OpenAI-compatible 传输适配器。

该适配器是生产模型端点的唯一 transport 实现。它只负责构造 chat completion
请求和映射传输异常，不承载重试、预算、审计、schema 校验或语义解释。
"""

from __future__ import annotations

from typing import Any, NoReturn

import litellm
from pydantic import SecretStr

from vet_agent.core import errors as core_errors
from vet_agent.core.llm.contracts import ChatCompletionRequest, NormalizedChatCompletion
from vet_agent.core.llm.normalization import normalize_chat_completion
from vet_agent.core.prompts import PromptMessage


def _convert_messages(messages: tuple[PromptMessage, ...]) -> list[dict[str, str]]:
    """将不可变 prompt 消息转换为 OpenAI-compatible 请求形态。

    :param messages: 已渲染模型消息。
    :return: 仅包含 role 与 content 的消息列表。
    """
    return [
        {"role": message.role.value, "content": message.content} for message in messages
    ]


def _extract_status_code(exception: Exception) -> int | None:
    """从异常对象提取 HTTP 状态码。

    :param exception: LiteLLM 或底层 HTTP 抛出的异常。
    :return: 可识别时返回整数状态码，否则返回 ``None``。
    """
    raw_status = getattr(exception, "status_code", None)
    if isinstance(raw_status, int) and not isinstance(raw_status, bool):
        return raw_status
    code_property = getattr(exception, "code", None)
    if isinstance(code_property, int) and not isinstance(code_property, bool):
        return code_property
    return None


def _exception_indicates_timeout(exception: Exception) -> bool:
    """判断异常是否表现为超时。

    :param exception: 底层传输异常。
    :return: 疑似超时时返回 ``True``。
    """
    text = f"{type(exception).__name__} {exception}".lower()
    return "timeout" in text or "timed out" in text


def _exception_indicates_transient_network(exception: Exception) -> bool:
    """判断异常是否表现为瞬时网络失败。

    :param exception: 底层传输异常。
    :return: 疑似网络瞬断时返回 ``True``。
    """
    text = f"{type(exception).__name__} {exception}".lower()
    indicators = (
        "connection error",
        "connection reset",
        "connection refused",
        "remote disconnected",
        "temporary failure",
        "name resolution",
        "network",
    )
    return any(indicator in text for indicator in indicators)


def _raise_mapped_transport_exception(exception: Exception) -> NoReturn:
    """将底层异常映射为稳定核心传输错误。

    :param exception: LiteLLM 或底层 HTTP 抛出的异常。
    :return: 本函数不返回；始终抛出映射后的核心错误。
    :raises vet_agent.core.errors.DependencyTimeoutError: 当请求疑似超时。
    :raises vet_agent.core.errors.RateLimitError: 当提供方返回 429。
    :raises vet_agent.core.errors.AuthenticationError: 当提供方返回 401。
    :raises vet_agent.core.errors.AuthorizationDeniedError: 当提供方返回 403。
    :raises vet_agent.core.errors.LLMTransportError: 当发生网络瞬断、408 或 5xx。
    :raises vet_agent.core.errors.LLMProtocolError: 当请求参数被提供方拒绝。
    :raises vet_agent.core.errors.DependencyUnavailableError: 当无法分类底层失败。
    """
    status_code = _extract_status_code(exception)
    context = {"provider_status": str(status_code or "unknown")}
    if _exception_indicates_timeout(exception):
        raise core_errors.DependencyTimeoutError(
            error_code=core_errors.CoreErrorCode.DEPENDENCY_TIMEOUT,
            message="模型服务调用超时",
            retryable=True,
            context=context,
            cause=exception,
        ) from exception
    if status_code == 429:
        raise core_errors.RateLimitError(
            error_code=core_errors.CoreErrorCode.RATE_LIMITED,
            message="模型服务限流",
            retryable=True,
            context=context,
            cause=exception,
        ) from exception
    if _exception_indicates_transient_network(exception):
        raise core_errors.LLMTransportError(
            error_code=core_errors.CoreErrorCode.LLM_TRANSPORT_FAILED,
            message="模型服务网络瞬断",
            retryable=True,
            context=context,
            cause=exception,
        ) from exception
    if status_code == 401:
        raise core_errors.AuthenticationError(
            error_code=core_errors.CoreErrorCode.AUTHENTICATION_FAILED,
            message="模型服务认证失败",
            context=context,
            cause=exception,
        ) from exception
    if status_code == 403:
        raise core_errors.AuthorizationDeniedError(
            error_code=core_errors.CoreErrorCode.AUTHORIZATION_DENIED,
            message="模型服务授权拒绝",
            context=context,
            cause=exception,
        ) from exception
    if status_code == 408 or status_code is not None and status_code >= 500:
        raise core_errors.LLMTransportError(
            error_code=core_errors.CoreErrorCode.LLM_TRANSPORT_FAILED,
            message="模型服务传输失败",
            retryable=True,
            context=context,
            cause=exception,
        ) from exception
    if status_code is not None and 400 <= status_code < 500:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型服务拒绝请求参数",
            context=context,
            cause=exception,
        ) from exception
    raise core_errors.DependencyUnavailableError(
        error_code=core_errors.CoreErrorCode.DEPENDENCY_UNAVAILABLE,
        message="模型服务不可用",
        context=context,
        cause=exception,
    ) from exception


class LiteLLMChatCompletionClient:
    """基于 ``litellm.acompletion`` 的生产传输客户端。

    :ivar _base_url: LiteLLM OpenAI-compatible 服务地址。
    :ivar _api_key: 进程内密钥对象。
    """

    def __init__(self, *, base_url: str, api_key: SecretStr) -> None:
        """初始化生产传输客户端。

        :param base_url: LiteLLM OpenAI-compatible 服务地址。
        :param api_key: 服务密钥；普通日志不得输出该值。
        :return: 无返回值；保存传输连接配置。
        """
        self._base_url = base_url
        self._api_key = api_key

    async def complete(
        self, request: ChatCompletionRequest
    ) -> NormalizedChatCompletion:
        """执行一次 LiteLLM 物理请求。

        :param request: 传输层请求对象。
        :return: 归一化的完成响应。
        :raises vet_agent.core.errors.CoreError: 当底层调用失败时映射为核心错误。
        """
        request_parameters: dict[str, Any] = {
            "model": request.model,
            "messages": _convert_messages(request.messages),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "timeout": request.timeout_seconds,
            "base_url": self._base_url,
            "api_key": self._api_key.get_secret_value(),
            "num_retries": 0,
            "extra_headers": {
                "x-vet-agent-request-id": request.request_id,
                "x-vet-agent-attempt-id": request.attempt_id,
                "x-vet-agent-prompt-id": request.prompt_id,
                "x-vet-agent-prompt-version": request.prompt_version,
            },
        }
        if request.response_format.value != "disabled":
            request_parameters["response_format"] = {
                "type": request.response_format.value
            }
        try:
            response = await litellm.acompletion(**request_parameters)
        except Exception as exc:  # noqa: BLE001 - transport failures must be mapped
            _raise_mapped_transport_exception(exc)
        return normalize_chat_completion(
            response=response, fallback_model=request.model
        )


__all__ = ["LiteLLMChatCompletionClient"]
