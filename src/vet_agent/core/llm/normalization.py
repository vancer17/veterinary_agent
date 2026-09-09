"""
OpenAI-compatible 完成响应协议归一。

该模块在厂商响应进入 JSON 解析前执行结构校验，拒绝空内容、截断、refusal、
tool call、缺失 usage 和非法 token 数；不做医学语义解释。
"""

from __future__ import annotations

from collections.abc import Mapping

from vet_agent.core import errors as core_errors
from vet_agent.core.llm.contracts import LLMUsage, NormalizedChatCompletion


def _read_field(container: object, field_name: str) -> object:
    """读取 dict 或对象风格响应字段。

    :param container: 提供方返回的 dict 或对象。
    :param field_name: 字段名。
    :return: 字段值；不存在时返回 ``None``。
    """
    if isinstance(container, Mapping):
        return container.get(field_name)
    return getattr(container, field_name, None)


def _read_non_negative_int(value: object, field_name: str) -> int:
    """读取并校验非负整数字段。

    :param value: 原始字段值。
    :param field_name: 字段名，用于错误上下文。
    :return: 非负整数。
    :raises vet_agent.core.errors.LLMProtocolError: 当值不是整数或为负数。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型响应 usage 字段非法",
            context={"usage_field": field_name},
        )
    return value


def normalize_chat_completion(
    *,
    response: object,
    fallback_model: str,
) -> NormalizedChatCompletion:
    """归一并校验一次模型完成响应。

    :param response: LiteLLM 返回的 dict 或对象。
    :param fallback_model: 提供方未返回模型名时使用的配置模型名。
    :return: 可进入 Strict JSON Gateway 的归一完成对象。
    :raises vet_agent.core.errors.LLMProtocolError: 当响应违反完成协议。
    """
    choices = _read_field(response, "choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型响应必须且仅包含一个 choice",
            context={
                "choice_count": str(len(choices))
                if isinstance(choices, list)
                else "invalid"
            },
        )
    choice = choices[0]
    message = _read_field(choice, "message")
    raw_content = _read_field(message, "content")
    refusal = _read_field(message, "refusal")
    tool_calls = _read_field(message, "tool_calls")
    function_call = _read_field(message, "function_call")
    finish_reason = _read_field(choice, "finish_reason")
    if not isinstance(raw_content, str) or not raw_content:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型响应 content 为空或非字符串",
        )
    if refusal:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型返回 refusal",
            context={"finish_reason": str(finish_reason)},
        )
    if tool_calls or function_call:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型返回禁止的 tool 或 function call",
        )
    if finish_reason != "stop":
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型完成原因非法",
            context={"finish_reason": str(finish_reason)},
        )
    usage_object = _read_field(response, "usage")
    prompt_tokens = _read_non_negative_int(
        _read_field(usage_object, "prompt_tokens"), "prompt_tokens"
    )
    completion_tokens = _read_non_negative_int(
        _read_field(usage_object, "completion_tokens"), "completion_tokens"
    )
    total_tokens = _read_non_negative_int(
        _read_field(usage_object, "total_tokens"), "total_tokens"
    )
    if total_tokens != prompt_tokens + completion_tokens:
        raise core_errors.LLMProtocolError(
            error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
            message="模型 token 总数与分项不一致",
        )
    response_id = _read_field(response, "id")
    response_model = _read_field(response, "model")
    return NormalizedChatCompletion(
        content=raw_content,
        response_id=response_id
        if isinstance(response_id, str) and response_id
        else None,
        model=response_model
        if isinstance(response_model, str) and response_model
        else fallback_model,
        finish_reason=str(finish_reason),
        usage=LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


__all__ = ["normalize_chat_completion"]
