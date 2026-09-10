"""
受限 prompt 模板渲染器。

该模块把声明的 JSON / TEXT 占位符渲染为 system / user 消息。渲染器只做
显式替换、类型校验、大小限制和标签转义，不支持表达式、循环、条件、include
或运行期配置读取，避免把动态输入提升为系统指令。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from html import escape

from vet_agent.core import errors as core_errors
from vet_agent.core.prompts.contracts import (
    PromptBinding,
    PromptMessage,
    PromptMessageRole,
    PromptPlaceholderKind,
    RenderedPrompt,
)

type PromptRenderValue = (
    None
    | bool
    | int
    | float
    | str
    | list["PromptRenderValue"]
    | dict[str, "PromptRenderValue"]
)

_PLACEHOLDER_PATTERN = re.compile(
    pattern=r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
)


def _is_valid_json_value(value: PromptRenderValue) -> bool:
    """判断运行期对象是否符合封闭 JSON 值协议。

    :param value: 待写入 JSON 占位符的运行期值。
    :return: 符合 null、boolean、number、string、array 或 object 协议时返回
        ``True``，否则返回 ``False``。
    """
    if value is None or isinstance(value, bool | int | float | str):
        return True
    if isinstance(value, list):
        return all(_is_valid_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_valid_json_value(item)
            for key, item in value.items()
        )
    return False


def _encode_json_value(value: PromptRenderValue) -> str:
    """将结构化输入编码为安全 canonical JSON 文本。

    :param value: 已通过封闭类型检查的 JSON 值。
    :return: 排序、紧凑且转义标签分隔符的 JSON 文本。
    :raises core_errors.ConfigurationError: 当值不可 JSON 序列化或包含
        ``NaN`` / ``Infinity``。
    """
    try:
        encoded = json.dumps(
            obj=value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt JSON 占位符不可序列化",
            context={"reasons": "placeholder_json_invalid"},
            cause=exc,
        ) from exc
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _encode_text_value(value: str) -> str:
    """将普通文本占位符编码为 XML 安全文本。

    :param value: 应用侧提供的字符串值。
    :return: 转义 ``&``、``<``、``>`` 与引号后的安全文本。
    """
    return escape(s=value, quote=True)


def _render_section(
    *,
    title: str,
    content: str,
    rendered_values: Mapping[str, str],
) -> str:
    """渲染单个模板章节并保留受控标题。

    :param title: 章节标题。
    :param content: 原始章节正文。
    :param rendered_values: 占位符名到已编码安全文本的映射。
    :return: 可拼入模型消息的完整章节文本。
    """

    def replace_placeholder(match: re.Match[str]) -> str:
        """返回当前占位符匹配的安全渲染值。

        :param match: 模板中的受限占位符匹配。
        :return: 对应占位符的已编码文本。
        :raises core_errors.ConfigurationError: 当模板中出现未声明占位符。
        """
        name = match.group("name")
        rendered = rendered_values.get(name)
        if rendered is None:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 模板包含未声明占位符",
                context={"reasons": f"placeholder_not_declared:{name}"},
            )
        return rendered

    body = _PLACEHOLDER_PATTERN.sub(
        repl=replace_placeholder,
        string=content,
    )
    return f"## {title}\n\n{body}"


class DefaultPromptRenderer:
    """把 ``PromptBinding`` 渲染为统一模型调用输入。

    渲染器位于 prompt registry 之后、``core.llm`` 之前，是动态语义输入进入
    模型消息前的唯一安全插值入口。
    """

    def render(
        self,
        *,
        binding: PromptBinding,
        values: Mapping[str, PromptRenderValue],
    ) -> RenderedPrompt:
        """渲染一个 exact version prompt 绑定。

        :param binding: registry 返回的资产与调用绑定。
        :param values: 占位符名到运行期值的映射。
        :return: 包含 system / user 消息和版本化记录的 ``RenderedPrompt``。
        :raises core_errors.ConfigurationError: 当变量缺失、多余、类型非法、
            超过长度限制或不可安全序列化。
        """
        invalid_reasons: list[str] = []
        if not all(isinstance(name, str) for name in values):
            invalid_reasons.append("variable_name_not_string")
        value_names = set(values)
        declared_specs = {
            placeholder.name: placeholder
            for placeholder in binding.asset.spec.placeholders
        }
        declared_names = set(declared_specs)
        missing_names = declared_names - value_names
        extra_names = value_names - declared_names
        if missing_names:
            invalid_reasons.append("variables_missing")
        if extra_names:
            invalid_reasons.append("variables_extra")

        rendered_values: dict[str, str] = {}
        for name, placeholder in declared_specs.items():
            value = values.get(name)
            if name not in value_names:
                continue
            if placeholder.kind is PromptPlaceholderKind.JSON:
                if not _is_valid_json_value(value):
                    invalid_reasons.append(f"variable_type_invalid:{name}")
                    continue
                rendered = _encode_json_value(value)
            elif placeholder.kind is PromptPlaceholderKind.TEXT:
                if not isinstance(value, str):
                    invalid_reasons.append(f"variable_type_invalid:{name}")
                    continue
                rendered = _encode_text_value(value)
            else:
                invalid_reasons.append(f"placeholder_kind_invalid:{name}")
                continue
            if len(rendered) > placeholder.max_rendered_chars:
                invalid_reasons.append(f"variable_too_long:{name}")
                continue
            rendered_values[name] = rendered

        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 渲染变量非法",
                context={
                    "prompt_id": binding.record.prompt_id,
                    "reasons": ",".join(invalid_reasons),
                },
            )

        messages: list[PromptMessage] = []
        for message_spec in binding.asset.spec.messages:
            sections = [
                _render_section(
                    title=title,
                    content=binding.asset.sections[title],
                    rendered_values=rendered_values,
                )
                for title in message_spec.sections
            ]
            messages.append(
                PromptMessage(
                    role=PromptMessageRole(message_spec.role),
                    content="\n\n".join(sections),
                ),
            )
        return RenderedPrompt(
            record=binding.record,
            messages=tuple(messages),
        )


__all__ = [
    "DefaultPromptRenderer",
    "PromptRenderValue",
]
