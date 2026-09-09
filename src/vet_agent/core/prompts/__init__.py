"""
版本化 prompt 的最小执行契约。

本模块不加载、不渲染、不拥有语义 prompt 文案；它只提供 ``core.llm`` 必须
消费的版本化输入形态，并确保模型调用无法脱离 PromptRecord 执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from vet_agent.core import errors as core_errors
from vet_agent.core.schema import StrictModel


class PromptMessageRole(StrEnum):
    """模型调用消息角色。

    ``core.llm`` 只接受稳定的最小角色集合，避免语义层把工具调用或自由
    agent 协议混入统一模型入口。
    """

    #: 系统级约束与任务边界。
    SYSTEM = "system"

    #: 已由 prompt renderer 安全构造的用户消息。
    USER = "user"


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """不可变模型调用消息。

    :ivar role: 消息角色。
    :ivar content: 已渲染消息内容；普通日志不得输出该字段。
    """

    role: PromptMessageRole
    content: str


@dataclass(frozen=True, slots=True)
class PromptRecord:
    """prompt 注册表中的版本化记录快照。

    :ivar prompt_id: 语义层定义的稳定 prompt 标识。
    :ivar prompt_version: 语义 prompt 版本。
    :ivar prompt_sha256: 模板内容摘要，用于审计和可复现性。
    :ivar model_role: 绑定的稳定模型角色名。
    :ivar output_schema: 绑定的严格输出 schema。
    :ivar template: 原始模板文本；仅在注册表和专用审计中使用。
    """

    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    model_role: str
    output_schema: type[StrictModel]
    template: str


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """已由核心渲染层生成的不可变调用输入。

    :ivar record: 版本化 prompt 绑定记录。
    :ivar messages: 严格顺序化的模型消息列表。
    """

    record: PromptRecord
    messages: tuple[PromptMessage, ...]


def _is_sha256_hex(value: str) -> bool:
    """判断字符串是否为 64 位十六进制 SHA-256。

    :param value: 待检查的摘要字符串。
    :return: 符合格式时返回 ``True``，否则返回 ``False``。
    """
    return len(value) == 64 and all(
        char in "0123456789abcdef" for char in value.lower()
    )


def validate_prompt_binding(
    *,
    model_role: str,
    output_schema: type[StrictModel],
    prompt: RenderedPrompt,
) -> None:
    """校验 prompt、模型角色与输出 schema 的调用绑定。

    :param model_role: 本次调用请求的稳定模型角色名。
    :param output_schema: 本次调用要求返回的严格 schema。
    :param prompt: 已渲染 prompt 与版本化绑定记录。
    :return: 无返回值；绑定合法时允许进入模型网关。
    :raises vet_agent.core.errors.ConfigurationError: 当 prompt 缺失、内容为空或
        role / schema / version / digest 绑定不合法。
    """
    record = prompt.record
    invalid_reasons: list[str] = []
    if not record.prompt_id:
        invalid_reasons.append("prompt_id_empty")
    if not record.prompt_version:
        invalid_reasons.append("prompt_version_empty")
    if not _is_sha256_hex(record.prompt_sha256):
        invalid_reasons.append("prompt_sha256_invalid")
    if not record.model_role or record.model_role != model_role:
        invalid_reasons.append("model_role_mismatch")
    if record.output_schema is not output_schema:
        invalid_reasons.append("output_schema_mismatch")
    if not prompt.messages:
        invalid_reasons.append("messages_empty")
    if any(not message.content for message in prompt.messages):
        invalid_reasons.append("message_content_empty")
    if invalid_reasons:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="PromptRecord 与模型调用绑定不一致",
            context={
                "prompt_id": record.prompt_id or "<empty>",
                "reasons": ",".join(invalid_reasons),
            },
        )


__all__ = [
    "PromptMessage",
    "PromptMessageRole",
    "PromptRecord",
    "RenderedPrompt",
    "validate_prompt_binding",
]
