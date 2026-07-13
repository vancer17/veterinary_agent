##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/rules.py
# 作用: 提供 NonmedicalPetCareAgent 的轻量确定性规则、信号策略、维度映射和安全自检辅助函数。
# 边界: 不实现业务路由、不执行 RAG、不调用 LLM、不维护完整养宠问题分类树。
##################################################################################################

from collections.abc import Iterable, Mapping
from hashlib import sha256
from time import perf_counter

from veterinary_agent.nonmedical_pet_care_agent.enums import (
    AdviceDimensionCode,
    CareDomain,
    NonmedicalRetrievalPurpose,
)

COMPONENT_NAME = "nonmedical_pet_care_agent"

NUTRITION_TERMS: frozenset[str] = frozenset(
    {"吃", "粮", "喂", "饭", "营养", "零食", "饮食", "挑食", "食欲", "增肥"}
)
BEHAVIOR_TERMS: frozenset[str] = frozenset(
    {"咬", "叫", "扑", "训练", "行为", "焦虑", "乱尿", "拆家", "社交"}
)
CARE_TERMS: frozenset[str] = frozenset(
    {"洗澡", "梳毛", "剪指甲", "牙", "耳", "护理", "清洁", "毛发"}
)
ENVIRONMENT_TERMS: frozenset[str] = frozenset(
    {"环境", "笼", "猫砂", "温度", "湿度", "空间", "搬家", "出行"}
)
EXERCISE_TERMS: frozenset[str] = frozenset(
    {"运动", "遛", "玩", "消耗", "活动", "跑", "散步"}
)
WEIGHT_TERMS: frozenset[str] = frozenset(
    {"胖", "瘦", "体重", "减肥", "增重", "肥胖", "体型"}
)

GENERAL_SPECIES_SCOPES: frozenset[str] = frozenset(
    {"unknown", "general", "pet", "pets", "dog_or_cat", "mixed"}
)
SIGNAL_STRENGTHS_FOR_BOUNDARY: frozenset[str] = frozenset({"L1", "L2"})
HARD_ESCALATION_STRENGTHS: frozenset[str] = frozenset({"L3"})
SAFETY_ESCALATION_CODES: frozenset[str] = frozenset(
    {"SAF-01", "SAF_01", "SAF01", "TOXIN", "HUMAN_MEDICATION"}
)
EXTREME_DIET_TERMS: frozenset[str] = frozenset(
    {"断食", "长期禁食", "只喂肉", "完全不喝水", "快速减肥", "饿几天"}
)
PUNITIVE_TRAINING_TERMS: frozenset[str] = frozenset(
    {"打它", "电击", "勒住", "关禁闭", "惩罚到", "按住揍"}
)
MEDICATION_TERMS: frozenset[str] = frozenset(
    {"剂量", "片", "毫克", "mg", "处方", "抗生素", "止痛药", "人用药"}
)
OVERPROMISE_TERMS: frozenset[str] = frozenset(
    {"一定会", "保证", "彻底解决", "肯定没事", "绝对安全"}
)


def elapsed_ms(started_at: float) -> int:
    """计算从指定单调时间到当前的毫秒耗时。

    :param started_at: perf_counter 返回的起始时间。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


def text_hash(value: str) -> str:
    """计算短文本 sha256 摘要。

    :param value: 待计算 hash 的文本。
    :return: 带 sha256 前缀的摘要字符串。
    """

    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


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
    :return: 原列表副本；无法读取时返回空列表。
    """

    if isinstance(value, list):
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
    :param default: 无法读取时返回的默认值。
    :return: 解析后的布尔值。
    """

    if isinstance(value, bool):
        return value
    return default


def strings_from_unknown_list(value: object) -> list[str]:
    """从未知列表中提取非空字符串。

    :param value: 需要读取的未知列表。
    :return: 去重且保序的非空字符串列表。
    """

    strings: list[str] = []
    for item in as_list(value):
        item_text = read_string(item)
        if item_text is not None and item_text not in strings:
            strings.append(item_text)
    return strings


def unique_strings(values: Iterable[object]) -> list[str]:
    """从任意可迭代值中提取去重字符串。

    :param values: 待归一的未知值序列。
    :return: 去重且保序的字符串列表。
    """

    result: list[str] = []
    for value in values:
        text = read_string(value)
        if text is not None and text not in result:
            result.append(text)
    return result


def contains_any(text: str, terms: Iterable[str]) -> bool:
    """判断文本是否包含任一关键词。

    :param text: 待扫描文本。
    :param terms: 关键词集合。
    :return: 若包含任一关键词则返回 True。
    """

    normalized = text.lower()
    return any(term.lower() in normalized for term in terms)


def care_domain_from_task_and_text(task_type: str, text: str) -> CareDomain:
    """根据任务类型和文本选择保守护理领域。

    :param task_type: VetTaskDecomposer 输出的任务类型。
    :param text: 当前规范化问题文本。
    :return: 非医疗护理领域。
    """

    task = task_type.upper()
    if task == "NUTRITION" or contains_any(text, NUTRITION_TERMS):
        return CareDomain.NUTRITION
    if task == "BEHAVIOR" or contains_any(text, BEHAVIOR_TERMS):
        return CareDomain.BEHAVIOR
    if task == "CARE" or contains_any(text, CARE_TERMS):
        return CareDomain.DAILY_CARE
    if contains_any(text, WEIGHT_TERMS):
        return CareDomain.WEIGHT_MANAGEMENT
    if contains_any(text, EXERCISE_TERMS):
        return CareDomain.EXERCISE
    if contains_any(text, ENVIRONMENT_TERMS):
        return CareDomain.ENVIRONMENT
    return CareDomain.GENERAL_PET_CARE


def dimension_from_value(value: object) -> AdviceDimensionCode | None:
    """从未知值中解析建议维度代码。

    :param value: 需要解析的未知值。
    :return: 合法建议维度代码；无法解析时返回 None。
    """

    text = read_string(value)
    if text is None:
        return None
    try:
        return AdviceDimensionCode(text)
    except ValueError:
        return None


def purpose_for_dimension(
    dimension_code: AdviceDimensionCode,
) -> NonmedicalRetrievalPurpose:
    """根据建议维度选择默认检索用途。

    :param dimension_code: 建议维度代码。
    :return: 默认非医疗检索用途。
    """

    if dimension_code is AdviceDimensionCode.RISK_BOUNDARY:
        return NonmedicalRetrievalPurpose.RISK_BOUNDARY
    if dimension_code is AdviceDimensionCode.PROFESSIONAL_ESCALATION:
        return NonmedicalRetrievalPurpose.RISK_BOUNDARY
    if dimension_code is AdviceDimensionCode.GRADUAL_PACE:
        return NonmedicalRetrievalPurpose.BEHAVIOR_GUIDANCE
    if dimension_code is AdviceDimensionCode.OBSERVATION_METRICS:
        return NonmedicalRetrievalPurpose.PET_CARE_PRINCIPLE
    return NonmedicalRetrievalPurpose.PET_CARE_PRINCIPLE


def signal_code(value: Mapping[str, object]) -> str:
    """从输入安全信号映射中读取信号码。

    :param value: 信号原始映射。
    :return: 归一后的信号码；缺失时返回 UNKNOWN。
    """

    return (
        read_string(value.get("code"))
        or read_string(value.get("signal_code"))
        or "UNKNOWN"
    )


def signal_strength(value: Mapping[str, object]) -> str:
    """从输入安全信号映射中读取强度。

    :param value: 信号原始映射。
    :return: 大写信号强度；缺失时返回 NOT_APPLICABLE。
    """

    return (
        read_string(value.get("strength"))
        or read_string(value.get("signal_strength"))
        or "NOT_APPLICABLE"
    ).upper()


def is_hard_escalation_signal(code: str, strength: str) -> bool:
    """判断信号是否要求非医疗链路停止普通建议。

    :param code: 信号码。
    :param strength: 信号强度。
    :return: 若必须升级或高风险降级则返回 True。
    """

    normalized_code = code.upper().replace("_", "-")
    return strength.upper() in HARD_ESCALATION_STRENGTHS or any(
        escalation_code in {normalized_code, code.upper()}
        for escalation_code in SAFETY_ESCALATION_CODES
    )


def requires_body_boundary(strength: str) -> bool:
    """判断信号强度是否需要正文嵌入风险边界。

    :param strength: 信号强度。
    :return: 若应嵌入观察或就医边界则返回 True。
    """

    return strength.upper() in SIGNAL_STRENGTHS_FOR_BOUNDARY


__all__: tuple[str, ...] = (
    "BEHAVIOR_TERMS",
    "CARE_TERMS",
    "COMPONENT_NAME",
    "ENVIRONMENT_TERMS",
    "EXERCISE_TERMS",
    "EXTREME_DIET_TERMS",
    "GENERAL_SPECIES_SCOPES",
    "MEDICATION_TERMS",
    "NUTRITION_TERMS",
    "OVERPROMISE_TERMS",
    "PUNITIVE_TRAINING_TERMS",
    "WEIGHT_TERMS",
    "as_list",
    "as_mapping",
    "care_domain_from_task_and_text",
    "contains_any",
    "dimension_from_value",
    "elapsed_ms",
    "is_hard_escalation_signal",
    "purpose_for_dimension",
    "read_bool",
    "read_string",
    "requires_body_boundary",
    "signal_code",
    "signal_strength",
    "strings_from_unknown_list",
    "text_hash",
    "unique_strings",
)
