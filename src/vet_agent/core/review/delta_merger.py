# =============================================================================
# 文件: src/vet_agent/core/review/delta_merger.py
# 作用: 确定性合并多个 Review Group 输出的稀疏 Delta。
# 范围: 覆盖同缺陷码去重、不同缺陷码保留、target 顺序归一、未知 target
#       拒绝与语义层声明的确定性冲突规则校验。
# 说明: 本文件不调用模型，不理解缺陷码含义，不把冲突交给 Repair LLM
#       自行调和，也不产生任何修复动作。
# =============================================================================
"""
Review Delta 确定性合并器。

该模块消费已经通过 group 校验的 ``ReviewGroupResult``，输出跨 group 合并
后的 ``MergedReviewDelta``。合并顺序只来自 plan / group / target 的系统
顺序，不依赖模型调用完成时间。
"""

from __future__ import annotations

from collections.abc import Sequence

from vet_agent.core import errors as core_errors
from vet_agent.core.review.contracts import (
    MergedReviewDelta,
    ReviewConflictRule,
    ReviewDeltaEntry,
    ReviewGroupResult,
)


def merge_review_deltas(
    *,
    results: Sequence[ReviewGroupResult],
    target_order: Sequence[str],
    conflict_rules: Sequence[ReviewConflictRule] = (),
) -> MergedReviewDelta:
    """合并多个 Review Group 的稀疏 Delta。

    :param results: 按 Review Plan 顺序排列的 group 执行结果。
    :param target_order: 当前语义快照的系统 target 顺序。
    :param conflict_rules: 语义层声明的同 target 不可共存缺陷码规则。
    :return: 按系统顺序归一后的完整 Delta。
    :raises core_errors.ConfigurationError: 当结果、target 顺序或冲突规则
        配置非法。
    :raises core_errors.ReviewConflictError: 当同一 target 的缺陷码集合触发
        显式冲突规则。
    """
    if not results:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Review Delta 合并结果为空",
            context={"reasons": "results_empty"},
        )
    if (
        not target_order
        or len(target_order) != len(set(target_order))
    ):
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Review Delta 合并 target 顺序非法",
            context={"reasons": "target_order_invalid"},
        )
    target_ranks = {
        selector: rank for rank, selector in enumerate(target_order)
    }
    targets_to_codes: dict[str, set[str]] = {}
    codes_to_targets: dict[str, set[str]] = {}
    for result in results:
        for entry in result.delta:
            code = entry.defect_code
            codes_to_targets.setdefault(code, set()).update(entry.targets)
            for selector in entry.targets:
                if selector not in target_ranks:
                    raise core_errors.ConfigurationError(
                        error_code=(
                            core_errors.CoreErrorCode.CONFIGURATION_INVALID
                        ),
                        message="Review Delta 引用未知 target",
                        context={
                            "group_id": result.group_id,
                            "target": selector,
                        },
                    )
                targets_to_codes.setdefault(selector, set()).add(code)

    entries: list[ReviewDeltaEntry] = []
    for code, selectors in codes_to_targets.items():
        ordered_targets = sorted(
            selectors,
            key=target_ranks.__getitem__,
        )
        entries.append(
            ReviewDeltaEntry(
                defect_code=code,
                targets=tuple(ordered_targets),
            ),
        )

    merged = MergedReviewDelta(entries=tuple(entries))
    _validate_conflicts(
        targets_to_codes=targets_to_codes,
        conflict_rules=conflict_rules,
    )
    return merged


def _validate_conflicts(
    *,
    targets_to_codes: dict[str, set[str]],
    conflict_rules: Sequence[ReviewConflictRule],
) -> None:
    """校验同一 target 上的缺陷码是否触发显式冲突。

    :param targets_to_codes: target 到缺陷码集合的运行期索引。
    :param conflict_rules: 语义层声明的冲突规则集合。
    :return: 无返回值；未触发冲突时允许进入缺陷簇聚合。
    :raises core_errors.ReviewConflictError: 当同一 target 同时包含冲突规则
        声明的全部缺陷码。
    """
    for rule in conflict_rules:
        for target, codes in targets_to_codes.items():
            if rule.defect_codes.issubset(codes):
                raise core_errors.ReviewConflictError(
                    error_code=core_errors.CoreErrorCode.REVIEW_CONFLICT,
                    message="同一 Review target 触发确定性冲突规则",
                    context={
                        "rule_id": rule.rule_id,
                        "target": target,
                        "defect_codes": ",".join(sorted(rule.defect_codes)),
                    },
                )


__all__ = [
    "merge_review_deltas",
]
