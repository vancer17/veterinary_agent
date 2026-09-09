"""
严格模型输出 schema 契约。

该模块提供所有模型输出共享的 ``StrictModel`` 基类和严格 JSON 解析网关。
解析发生在语义解释之前；本模块不理解兽医语义，也不用另一个模型修复 JSON。
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from vet_agent.core import errors as core_errors
from vet_agent.core.errors import SchemaValidationError, StrictJSONValidationError

T = TypeVar("T", bound="StrictModel")


class StrictModel(BaseModel):
    """所有模型输出 schema 的严格基类。

    模型输出对象禁止额外字段、禁止隐式类型转换，并在校验成功后保持不可变，
    以避免语义层解释阶段发生结构漂移。
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _reject_json_constant(value: str) -> Any:
    """拒绝 JSON 标准外的常量值。

    :param value: JSON 解析器遇到的非标准常量文本。
    :return: 本函数不返回；显式抛出严格 JSON 错误。
    :raises StrictJSONValidationError: 当输出包含 ``NaN``、``Infinity`` 等值。
    """
    raise StrictJSONValidationError(
        error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
        message="模型输出包含非法 JSON 常量",
        context={"json_constant": value},
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造 JSON 对象并拒绝重复字段名。

    :param pairs: JSON 解析器传入的字段名与值列表。
    :return: 无重复字段名的 JSON 对象。
    :raises StrictJSONValidationError: 当同一对象中出现重复字段名。
    """
    result: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in pairs:
        if key in seen:
            raise StrictJSONValidationError(
                error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
                message="模型输出 JSON 对象包含重复字段名",
                context={"duplicated_field": key},
            )
        seen.add(key)
        result[key] = value
    return result


def _decode_single_json_object(content: str) -> dict[str, Any]:
    """解码且仅解码一个 JSON 对象。

    :param content: 模型返回的原始文本内容。
    :return: 待 schema 校验的 JSON 对象。
    :raises StrictJSONValidationError: 当内容为空、非 JSON、非对象、包含注释、
        代码块包裹、字符串化 JSON 或多个 JSON 值。
    """
    if not content or not content.strip():
        raise StrictJSONValidationError(
            error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
            message="模型输出内容为空",
        )
    if "```" in content:
        raise StrictJSONValidationError(
            error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
            message="模型输出包含 Markdown 代码块",
        )
    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    try:
        value, end = decoder.raw_decode(content)
    except (json.JSONDecodeError, StrictJSONValidationError) as exc:
        raise StrictJSONValidationError(
            error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
            message="模型输出不是严格 JSON",
            context={"parse_stage": "raw_decode"},
            cause=exc,
        ) from exc
    if not isinstance(value, dict):
        raise StrictJSONValidationError(
            error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
            message="模型输出顶层必须是 JSON 对象",
            context={"actual_type": type(value).__name__},
        )
    if content[end:].strip():
        raise StrictJSONValidationError(
            error_code=core_errors.CoreErrorCode.STRICT_JSON_INVALID,
            message="模型输出包含多个 JSON 值或尾部文本",
            context={"parse_stage": "trailing_content"},
        )
    return value


class StrictJSONGateway:
    """将模型 content 转换为严格 schema 对象的网关。

    该网关是模型输出进入语义解释前的唯一结构入口，负责拒绝协议外 JSON，
    并调用 strict Pydantic 校验；它不做医学判断和宽松修复。
    """

    def parse(self, *, content: str, output_schema: type[T]) -> T:
        """解析并校验单一严格 JSON 对象。

        :param content: 模型返回的原始文本内容。
        :param output_schema: 必须继承 ``StrictModel`` 的输出 schema。
        :return: 校验成功的不可变 typed schema 对象。
        :raises StrictJSONValidationError: 当 content 不是单一严格 JSON 对象。
        :raises SchemaValidationError: 当字段、类型或额外字段不符合 schema。
        """
        candidate = _decode_single_json_object(content)
        try:
            return output_schema.model_validate(candidate)
        except ValidationError as exc:
            raise SchemaValidationError(
                error_code=core_errors.CoreErrorCode.SCHEMA_INVALID,
                message="模型输出未通过严格 schema 校验",
                context={"schema_name": output_schema.__name__},
                cause=exc,
            ) from exc


__all__ = [
    "SchemaValidationError",
    "StrictJSONGateway",
    "StrictJSONValidationError",
    "StrictModel",
]
