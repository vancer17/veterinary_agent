# =============================================================================
# 文件: src/vet_agent/core/repair/__init__.py
# 作用: 作为 Targeted Repair 基础设施的顶层公共入口。
# 范围: 暴露修复任务、修复计划、执行结果、语义层输出校验端口与调度器。
# 说明: 语义模块必须通过本包顶层暴露的 API 使用 repair 能力，不得直接引用
#       内部实现模块，也不得绕过统一 prompt 与模型入口。
# =============================================================================
"""Targeted Repair 基础设施包。"""

from __future__ import annotations

from vet_agent.core.repair.contracts import (
    RepairOutputValidator,
    RepairPlanSpec,
    RepairTask,
    RepairTaskResult,
    validate_repair_target,
)
from vet_agent.core.repair.scheduler import TargetedRepairScheduler

__all__ = [
    "RepairOutputValidator",
    "RepairPlanSpec",
    "RepairTask",
    "RepairTaskResult",
    "TargetedRepairScheduler",
    "validate_repair_target",
]
