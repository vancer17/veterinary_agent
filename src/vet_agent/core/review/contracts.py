# =============================================================================
# 文件: src/vet_agent/core/review/contracts.py
# 作用: 定义 Grouped Review、稀疏 Delta 合并与缺陷簇聚合的公共工程契约。
# 范围: 覆盖 Review target、Review group、Review plan、Delta、缺陷短语、
#       冲突规则与执行结果的数据形态及启动期闭合校验。
# 说明: 本文件只承载工程协议，不理解缺陷码的兽医语义，不定义修复动作，
#       也不持有任何业务 prompt 文案或长期身份。
# =============================================================================
"""
Grouped Review 公共契约。

这些对象是语义预处理各子层进入 ``core.review`` 的稳定数据形态。语义层
提供 group 配置、缺陷码 allowlist 与目标 selector 集合；本模块只保证
工程结构闭合，不解释缺陷含义，也不推导修复动作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from vet_agent.core import errors as core_errors
from vet_agent.core.llm import LLMResponseMetadata
from vet_agent.core.schema import StrictModel


class ReviewTargetKind(StrEnum):
    """Review target 的工程分类。

    分类只用于 selector namespace 与缺陷簇聚合，不表示语义对象的业务
    权威身份，也不得写入宠物档案或长期记忆。
    """

    #: 当前 turn 的整体输入目标，仅对应 ``t0``。
    TURN = "turn"

    #: Turn Intent 控制协议字段目标，对应 ``c*``。
    CONTROL = "control"

    #: Turn Intent request item 目标，对应 ``r*``。
    REQUEST_ITEM = "request_item"

    #: 当前回合 semantic anchor 目标，对应 ``a*``。
    ANCHOR = "anchor"

    #: 只读可信宠物事实投影目标，对应 ``p*``。
    PET_FACT = "pet_fact"

    #: 系统物化后的联合上下文 scope 目标，对应 ``s*``。
    SCOPE = "scope"

    #: contextual query proposition 目标，对应 ``q*``。
    PROPOSITION = "proposition"


class SparseReviewDelta(StrictModel):
    """稀疏 Review 输出 schema 的公共基类。

    业务 schema 必须把每个缺陷码声明为 ``list[str] | None``；字段省略表示
    无缺陷，空数组、``null`` 和未知字段均不允许作为合法无缺陷结果。
    """


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    """一个 Review 可定位的当前快照目标。

    :ivar selector: 系统分配的本地 selector，不是长期身份。
    :ivar kind: target 的工程分类，用于 namespace 与聚合边界。
    """

    selector: str
    kind: ReviewTargetKind

    def __post_init__(self) -> None:
        """校验 target selector 与工程分类的绑定。

        :return: 无返回值；selector 格式或 namespace 不合法时显式失败。
        :raises core_errors.ConfigurationError: 当 selector 与 target kind
            不属于同一本地 namespace。
        """
        patterns: dict[ReviewTargetKind, str] = {
            ReviewTargetKind.TURN: r"t0",
            ReviewTargetKind.CONTROL: r"c[0-9]+",
            ReviewTargetKind.REQUEST_ITEM: r"r[0-9]+",
            ReviewTargetKind.ANCHOR: r"a[0-9]+",
            ReviewTargetKind.PET_FACT: r"p[0-9]+",
            ReviewTargetKind.SCOPE: r"s[0-9]+",
            ReviewTargetKind.PROPOSITION: r"q[0-9]+",
        }
        if re.fullmatch(patterns[self.kind], self.selector) is None:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review target selector 与 target kind 不匹配",
                context={
                    "selector": self.selector,
                    "target_kind": self.kind.value,
                },
            )


@dataclass(frozen=True, slots=True)
class ReviewGroupSpec:
    """一个 Review Group 的启动期配置。

    :ivar group_id: 语义层定义的稳定 group 标识。
    :ivar prompt_id: group 绑定的版本化 prompt 标识。
    :ivar prompt_version: group 绑定的 exact prompt 版本。
    :ivar allowed_defect_codes: 该 group 独占的缺陷码 allowlist。
    :ivar allowed_targets: 该 group 可输出的当前快照 target 集合。
    :ivar target_kind: 该 group 的 target 工程分类。
    :ivar output_schema: 该 group 的稀疏 Review 输出 schema。
    """

    group_id: str
    prompt_id: str
    prompt_version: str
    allowed_defect_codes: tuple[str, ...]
    allowed_targets: tuple[ReviewTarget, ...]
    target_kind: ReviewTargetKind
    output_schema: type[SparseReviewDelta]

    def __post_init__(self) -> None:
        """校验 Review Group 的字段、schema 与 target 闭合性。

        :return: 无返回值；任一工程绑定不闭合时启动期显式失败。
        :raises core_errors.ConfigurationError: 当标识、allowlist、target、
            schema 或 prompt 版本配置非法。
        """
        invalid_reasons: list[str] = []
        if not self.group_id:
            invalid_reasons.append("group_id_empty")
        if not self.prompt_id:
            invalid_reasons.append("prompt_id_empty")
        if (
            not self.prompt_version
            or self.prompt_version.casefold() == "latest"
        ):
            invalid_reasons.append("prompt_version_not_exact")
        if not self.allowed_defect_codes:
            invalid_reasons.append("defect_codes_empty")
        if len(self.allowed_defect_codes) != len(set(self.allowed_defect_codes)):
            invalid_reasons.append("defect_codes_duplicated")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_]*", code) is None
            for code in self.allowed_defect_codes
        ):
            invalid_reasons.append("defect_code_format_invalid")
        if not self.allowed_targets:
            invalid_reasons.append("targets_empty")
        selectors = [target.selector for target in self.allowed_targets]
        if len(selectors) != len(set(selectors)):
            invalid_reasons.append("targets_duplicated")
        if any(target.kind is not self.target_kind for target in self.allowed_targets):
            invalid_reasons.append("target_kind_mismatch")
        if not (
            isinstance(self.output_schema, type)
            and issubclass(self.output_schema, SparseReviewDelta)
        ):
            invalid_reasons.append("output_schema_invalid")
        else:
            schema_fields = tuple(self.output_schema.model_fields)
            if set(schema_fields) != set(self.allowed_defect_codes):
                invalid_reasons.append("output_schema_fields_mismatch")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Group 配置非法",
                context={
                    "group_id": self.group_id or "<empty>",
                    "reasons": ",".join(invalid_reasons),
                },
            )


@dataclass(frozen=True, slots=True)
class ReviewPlanSpec:
    """一次 Grouped Review 的计划配置。

    :ivar groups: 按执行顺序排列的 Review Group 集合。
    """

    groups: tuple[ReviewGroupSpec, ...]

    def __post_init__(self) -> None:
        """校验多个 Review Group 之间的缺陷码边界。

        :return: 无返回值；group 或缺陷码重复时显式失败。
        :raises core_errors.ConfigurationError: 当计划为空、group 重复或
            缺陷码跨 group 重叠。
        """
        invalid_reasons: list[str] = []
        group_ids = [group.group_id for group in self.groups]
        defect_codes = [
            code
            for group in self.groups
            for code in group.allowed_defect_codes
        ]
        if not self.groups:
            invalid_reasons.append("groups_empty")
        if len(group_ids) != len(set(group_ids)):
            invalid_reasons.append("group_ids_duplicated")
        if len(defect_codes) != len(set(defect_codes)):
            invalid_reasons.append("defect_codes_cross_group_duplicated")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Plan 配置非法",
                context={"reasons": ",".join(invalid_reasons)},
            )


@dataclass(frozen=True, slots=True)
class ReviewDeltaEntry:
    """一个缺陷码及其受影响 target 的稀疏 Delta 条目。

    :ivar defect_code: 已通过 group allowlist 的缺陷码。
    :ivar targets: 按当前快照顺序排序的不可变 target selector 集合。
    """

    defect_code: str
    targets: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验 Delta 条目的非空性与唯一性。

        :return: 无返回值；缺陷码或 target 集合非法时显式失败。
        :raises core_errors.ConfigurationError: 当条目为空或 target 重复。
        """
        if (
            not self.defect_code
            or re.fullmatch(r"[a-z][a-z0-9_]*", self.defect_code) is None
        ):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Delta 缺陷码非法",
                context={"defect_code": self.defect_code or "<empty>"},
            )
        if not self.targets or len(self.targets) != len(set(self.targets)):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Delta target 集合非法",
                context={"defect_code": self.defect_code},
            )


@dataclass(frozen=True, slots=True)
class ReviewGroupResult:
    """一个 Review Group 的执行结果。

    :ivar group_id: 完成 or 失败前对应的 group 标识。
    :ivar delta: 该 group 输出的稀疏缺陷 Delta。
    :ivar llm_metadata: 唯一模型入口返回的脱敏执行 metadata。
    """

    group_id: str
    delta: tuple[ReviewDeltaEntry, ...]
    llm_metadata: LLMResponseMetadata

    def __post_init__(self) -> None:
        """校验 group 执行结果的基本闭合性。

        :return: 无返回值；结果标识或 Delta 非法时显式失败。
        :raises core_errors.ConfigurationError: 当 group 标识为空或同一
            缺陷码重复出现。
        """
        defect_codes = [entry.defect_code for entry in self.delta]
        if not self.group_id:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Group 结果标识为空",
                context={"reasons": "group_id_empty"},
            )
        if len(defect_codes) != len(set(defect_codes)):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Group 结果包含重复缺陷码",
                context={"group_id": self.group_id},
            )


@dataclass(frozen=True, slots=True)
class MergedReviewDelta:
    """跨 Group 确定性合并后的完整稀疏 Delta。

    :ivar entries: 按计划顺序保留的缺陷码级 Delta 条目。
    """

    entries: tuple[ReviewDeltaEntry, ...]

    def __post_init__(self) -> None:
        """校验合并结果中缺陷码的唯一性。

        :return: 无返回值；合并结果重复时显式失败。
        :raises core_errors.ConfigurationError: 当同一缺陷码出现多次。
        """
        defect_codes = [entry.defect_code for entry in self.entries]
        if len(defect_codes) != len(set(defect_codes)):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="合并后的 Review Delta 包含重复缺陷码",
                context={"reasons": "defect_codes_duplicated"},
            )


@dataclass(frozen=True, slots=True)
class DefectPhrase:
    """缺陷码到人工中文短语的映射条目。

    :ivar defect_code: 语义层定义的缺陷码。
    :ivar phrase: 供 Repair prompt 使用的固定中文观察短语。
    """

    defect_code: str
    phrase: str

    def __post_init__(self) -> None:
        """校验缺陷短语条目的基本格式。

        :return: 无返回值；缺陷码或短语为空时显式失败。
        :raises core_errors.ConfigurationError: 当映射条目不完整。
        """
        if (
            re.fullmatch(r"[a-z][a-z0-9_]*", self.defect_code) is None
            or not self.phrase.strip()
        ):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="缺陷短语映射非法",
                context={"defect_code": self.defect_code or "<empty>"},
            )


@dataclass(frozen=True, slots=True)
class DefectPhraseCatalog:
    """一个语义域提供的缺陷短语目录。

    :ivar entries: 可用于缺陷簇聚合的不可变短语映射集合。
    """

    entries: tuple[DefectPhrase, ...]

    def __post_init__(self) -> None:
        """校验短语目录的完整性与唯一性。

        :return: 无返回值；目录为空、缺陷码重复或短语重复时显式失败。
        :raises core_errors.ConfigurationError: 当目录配置不闭合。
        """
        defect_codes = [entry.defect_code for entry in self.entries]
        phrases = [entry.phrase for entry in self.entries]
        invalid_reasons: list[str] = []
        if not self.entries:
            invalid_reasons.append("entries_empty")
        if len(defect_codes) != len(set(defect_codes)):
            invalid_reasons.append("defect_codes_duplicated")
        if len(phrases) != len(set(phrases)):
            invalid_reasons.append("phrases_duplicated")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="缺陷短语目录非法",
                context={"reasons": ",".join(invalid_reasons)},
            )

    def phrase_for(self, *, defect_code: str) -> str:
        """查询缺陷码对应的固定中文短语。

        :param defect_code: 已进入合并 Delta 的缺陷码。
        :return: 人工维护的中文观察短语。
        :raises core_errors.ConfigurationError: 当缺陷码未在目录中声明。
        """
        for entry in self.entries:
            if entry.defect_code == defect_code:
                return entry.phrase
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="缺陷码缺少中文短语映射",
            context={"defect_code": defect_code},
        )


@dataclass(frozen=True, slots=True)
class ReviewConflictRule:
    """由语义层声明的一组不可共存缺陷码。

    :ivar rule_id: 语义层定义的冲突规则标识。
    :ivar defect_codes: 同一 target 上不得同时出现的缺陷码集合。
    """

    rule_id: str
    defect_codes: frozenset[str]

    def __post_init__(self) -> None:
        """校验冲突规则的工程闭合性。

        :return: 无返回值；规则标识或缺陷码集合非法时显式失败。
        :raises core_errors.ConfigurationError: 当规则为空或不足两个缺陷码。
        """
        if not self.rule_id or len(self.defect_codes) < 2:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review 冲突规则非法",
                context={"rule_id": self.rule_id or "<empty>"},
            )


@dataclass(frozen=True, slots=True)
class DefectCluster:
    """按 target 聚合后的完整缺陷簇。

    :ivar target: 当前快照中的本地 target selector。
    :ivar target_kind: target 的工程分类。
    :ivar defect_codes: 该 target 上的完整缺陷码集合。
    :ivar defect_phrases: 与缺陷码顺序一致的中文观察短语集合。
    """

    target: str
    target_kind: ReviewTargetKind
    defect_codes: tuple[str, ...]
    defect_phrases: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验缺陷簇的完整性。

        :return: 无返回值；target、缺陷码或短语集合非法时显式失败。
        :raises core_errors.ConfigurationError: 当缺陷簇不闭合。
        """
        if (
            not self.target
            or not self.defect_codes
            or len(self.defect_codes) != len(set(self.defect_codes))
            or len(self.defect_codes) != len(self.defect_phrases)
        ):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="缺陷簇结构非法",
                context={"target": self.target or "<empty>"},
            )


__all__ = [
    "DefectCluster",
    "DefectPhrase",
    "DefectPhraseCatalog",
    "MergedReviewDelta",
    "ReviewConflictRule",
    "ReviewDeltaEntry",
    "ReviewGroupResult",
    "ReviewGroupSpec",
    "ReviewPlanSpec",
    "ReviewTarget",
    "ReviewTargetKind",
    "SparseReviewDelta",
]
