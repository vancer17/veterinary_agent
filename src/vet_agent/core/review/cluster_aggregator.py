# =============================================================================
# 文件: src/vet_agent/core/review/cluster_aggregator.py
# 作用: 将合并后的 Review Delta 按 target 聚合为缺陷簇。
# 范围: 覆盖 target 顺序归一、缺陷短语映射、target kind 绑定与缺陷簇
#       输出结构校验。
# 说明: 本文件不理解缺陷含义，不推导修复动作，不调用模型，也不把
#       selector 当作长期身份或权威事实 ID。
# =============================================================================
"""
Review 缺陷簇聚合器。

该模块把跨 group 的稀疏 Delta 转换成 target 级缺陷簇。一个 target 只产生
一个 cluster；后续 Targeted Repair 必须消费完整缺陷簇，而不是按缺陷码
拆分修复任务。
"""

from __future__ import annotations

from collections.abc import Sequence

from vet_agent.core import errors as core_errors
from vet_agent.core.review.contracts import (
    DefectCluster,
    DefectPhraseCatalog,
    MergedReviewDelta,
    ReviewTarget,
)


def aggregate_defect_clusters(
    *,
    delta: MergedReviewDelta,
    targets: Sequence[ReviewTarget],
    phrase_catalog: DefectPhraseCatalog,
) -> tuple[DefectCluster, ...]:
    """按 target 聚合合并后的 Review Delta。

    :param delta: 已由 Delta Merger 归一化的完整缺陷 Delta。
    :param targets: 当前语义快照的系统 target 集合与顺序。
    :param phrase_catalog: 语义域提供的缺陷码到中文短语目录。
    :return: 按当前快照 target 顺序排列的缺陷簇集合。
    :raises core_errors.ConfigurationError: 当 target 集合、Delta 引用或
        缺陷短语映射不闭合。
    """
    if not targets or len(targets) != len({target.selector for target in targets}):
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="缺陷簇聚合 target 集合非法",
            context={"reasons": "targets_invalid"},
        )
    target_index = {target.selector: target for target in targets}
    delta_targets = {
        selector
        for entry in delta.entries
        for selector in entry.targets
    }
    unknown_targets = delta_targets - set(target_index)
    if unknown_targets:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="缺陷簇聚合发现未知 target",
            context={"targets": ",".join(sorted(unknown_targets))},
        )

    clusters: list[DefectCluster] = []
    for target in targets:
        target_codes: list[str] = []
        for entry in delta.entries:
            if target.selector in entry.targets:
                target_codes.append(entry.defect_code)
        if not target_codes:
            continue
        target_phrases = [
            phrase_catalog.phrase_for(defect_code=code)
            for code in target_codes
        ]
        clusters.append(
            DefectCluster(
                target=target.selector,
                target_kind=target.kind,
                defect_codes=tuple(target_codes),
                defect_phrases=tuple(target_phrases),
            ),
        )
    return tuple(clusters)


__all__ = [
    "aggregate_defect_clusters",
]
