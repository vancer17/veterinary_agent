"""
prompt 与模型调用协议的一致性校验。

该模块在渲染结果进入 ``core.llm`` 前复核版本、摘要、模型角色、输出
schema 和消息内容，防止语义层绕过 registry 直接构造不完整调用输入。
"""

from __future__ import annotations

from vet_agent.core import errors as core_errors
from vet_agent.core.prompts.contracts import RenderedPrompt
from vet_agent.core.schema import StrictModel


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
    :raises vet_agent.core.errors.ConfigurationError: 当 prompt 缺失、内容为空
        或 role / schema / version / digest 绑定不合法。
    """
    record = prompt.record
    invalid_reasons: list[str] = []
    if not record.prompt_id:
        invalid_reasons.append("prompt_id_empty")
    if not record.prompt_version or record.prompt_version.casefold() == "latest":
        invalid_reasons.append("prompt_version_invalid")
    if not _is_sha256_hex(record.prompt_sha256):
        invalid_reasons.append("prompt_sha256_invalid")
    if not record.template:
        invalid_reasons.append("template_empty")
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
    "validate_prompt_binding",
]
