##################################################################################################
# 文件: src/veterinary_agent/education_agent/rules.py
# 作用: 提供 EducationAgent 内部使用的稳定规则常量、轻量解析函数和检索用途映射。
# 边界: 不调用模型、不读取上下文来源、不执行 RAG、不生成草稿；仅服务本包内编排实现。
##################################################################################################

from collections.abc import Iterable, Mapping
from hashlib import sha256
from time import perf_counter

from veterinary_agent.education_agent.enums import (
    EducationRetrievalPurpose,
    ExplanationDimensionCode,
)

COMPONENT_NAME = "education_agent"
GENERAL_SPECIES_SCOPES: frozenset[str] = frozenset({"unknown", "general", "all"})
MEDICATION_TERMS: tuple[str, ...] = (
    "药",
    "补剂",
    "剂量",
    "服用",
    "使用",
    "mg/kg",
    "毫克",
)
LAB_TERMS: tuple[str, ...] = (
    "化验",
    "检查",
    "指标",
    "血常规",
    "生化",
    "参考区间",
)
FORBIDDEN_FORMAT_TERMS: tuple[str, ...] = (
    "L1_TRIAGE",
    "L2_DIRECTION",
    "L3_DIFFERENTIAL",
    "L4_CARE_PLAN",
    "四层",
    "鉴别诊断",
)
T4_RISK_TERMS: tuple[str, ...] = ("mg/kg", "每公斤", "精确剂量", "处方剂量")
REFERENCE_RANGE_RISK_TERMS: tuple[str, ...] = (
    "参考区间",
    "正常范围",
    "异常标记",
)


def elapsed_ms(started_at: float) -> int:
    """计算从单调时钟起点到当前的毫秒数。

    :param started_at: perf_counter 返回的起始时间。
    :return: 非负毫秒耗时。
    """

    return max(0, int((perf_counter() - started_at) * 1000))


def text_hash(value: str) -> str:
    """计算文本的稳定 SHA-256 摘要。

    :param value: 待摘要的文本。
    :return: 带 sha256 前缀的摘要字符串。
    """

    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def as_list(value: object) -> list[object]:
    """将未知值安全读取为列表。

    :param value: 需要读取的未知值。
    :return: 若输入为列表或元组则返回普通列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def read_bool(value: object, *, default: bool) -> bool:
    """从未知值中读取布尔值。

    :param value: 需要读取的未知值。
    :param default: 无法读取布尔值时使用的默认值。
    :return: 解析后的布尔值或默认值。
    """

    if isinstance(value, bool):
        return value
    return default


def unique_strings(values: Iterable[str]) -> list[str]:
    """按出现顺序对字符串列表去重。

    :param values: 待去重的字符串序列。
    :return: 去除空白和重复后的字符串列表。
    """

    normalized: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
    return normalized


def dimension_from_value(value: object) -> ExplanationDimensionCode | None:
    """从未知值解析解释维度代码。

    :param value: 需要解析的未知值。
    :return: 可识别的解释维度；无法识别时返回 None。
    """

    text = read_string(value)
    if text is None:
        return None
    try:
        return ExplanationDimensionCode(text)
    except ValueError:
        return None


def purpose_for_dimension(
    dimension_code: ExplanationDimensionCode,
) -> EducationRetrievalPurpose:
    """根据解释维度选择默认 RAG 检索用途。

    :param dimension_code: 解释维度代码。
    :return: 对应的科普 RAG 检索用途。
    """

    if dimension_code is ExplanationDimensionCode.RED_FLAGS:
        return EducationRetrievalPurpose.EDUCATION_RED_FLAG_BOUNDARY
    if dimension_code is ExplanationDimensionCode.MEDICATION_BOUNDARY:
        return EducationRetrievalPurpose.EDUCATION_MEDICATION_BOUNDARY
    if dimension_code is ExplanationDimensionCode.CHECKUP_PRINCIPLES:
        return EducationRetrievalPurpose.EDUCATION_LAB_INTERPRETATION
    return EducationRetrievalPurpose.EDUCATION_EXPLANATION


def strings_from_unknown_list(value: object) -> list[str]:
    """从未知列表值中提取非空字符串。

    :param value: 待解析的未知列表值。
    :return: 非空字符串列表。
    """

    return [
        item.strip()
        for item in as_list(value)
        if isinstance(item, str) and item.strip()
    ]


def contains_any(text: str, terms: Iterable[str]) -> bool:
    """判断文本是否包含任一指定片段。

    :param text: 待检查文本。
    :param terms: 待匹配的片段集合。
    :return: 若命中任一片段则返回 True。
    """

    normalized = text.lower()
    return any(term.lower() in normalized for term in terms)


__all__: tuple[str, ...] = (
    "COMPONENT_NAME",
    "FORBIDDEN_FORMAT_TERMS",
    "GENERAL_SPECIES_SCOPES",
    "LAB_TERMS",
    "MEDICATION_TERMS",
    "REFERENCE_RANGE_RISK_TERMS",
    "T4_RISK_TERMS",
    "as_list",
    "as_mapping",
    "contains_any",
    "dimension_from_value",
    "elapsed_ms",
    "purpose_for_dimension",
    "read_bool",
    "read_string",
    "strings_from_unknown_list",
    "text_hash",
    "unique_strings",
)
