# =============================================================================
# 文件: src/vet_agent/core/review/__init__.py
# 作用: 作为 Grouped Review 基础设施的顶层公共入口。
# 范围: 暴露 review 契约、group 执行器、Delta 合并器与缺陷簇聚合器。
# 说明: 语义模块必须通过本包顶层暴露的 API 使用 review 能力，不得直接
#       引用内部实现模块，也不得绕过统一 prompt 与模型入口。
# =============================================================================
"""Grouped Review 基础设施包。"""

from __future__ import annotations

from vet_agent.core.review.cluster_aggregator import aggregate_defect_clusters
from vet_agent.core.review.contracts import (
    DefectCluster,
    DefectPhrase,
    DefectPhraseCatalog,
    MergedReviewDelta,
    ReviewConflictRule,
    ReviewDeltaEntry,
    ReviewGroupResult,
    ReviewGroupSpec,
    ReviewPlanSpec,
    ReviewTarget,
    ReviewTargetKind,
    SparseReviewDelta,
)
from vet_agent.core.review.delta_merger import merge_review_deltas
from vet_agent.core.review.group_runner import (
    GroupedReviewRunner,
    ReviewGroupRunner,
)

__all__ = [
    "DefectCluster",
    "DefectPhrase",
    "DefectPhraseCatalog",
    "GroupedReviewRunner",
    "MergedReviewDelta",
    "ReviewConflictRule",
    "ReviewDeltaEntry",
    "ReviewGroupResult",
    "ReviewGroupRunner",
    "ReviewGroupSpec",
    "ReviewPlanSpec",
    "ReviewTarget",
    "ReviewTargetKind",
    "SparseReviewDelta",
    "aggregate_defect_clusters",
    "merge_review_deltas",
]
