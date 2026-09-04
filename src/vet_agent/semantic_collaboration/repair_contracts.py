"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/repair_contracts.py
作用：定义受限语义协作 DAG M09 的确定性修复计划契约。
范围：覆盖通用修复 lane、修复任务身份、修复预算、clarification gap 路由、
      inventory 修复优先级、下游 stale 记录、计划验证结果与 M11 绑定端口。
说明：本文件不调用修复 LLM、不生成 typed patch、不修改 claims、不写数据库，
      也不把来源绑定缺失或未分类问题伪装成可自动修复问题。
=============================================================================
"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_contracts import (
    ClaimCoverageReviewDimension,
    ClaimFaithfulnessReviewDimension,
    ClarificationGapProposal,
    SemanticReviewBundle,
)

ReviewRepairDimension = ClaimCoverageReviewDimension | ClaimFaithfulnessReviewDimension


class SemanticRepairLane(StrEnum):
    """表示 M09 生成的通用修复上下文粒度。

    :return: 无返回值；生产只按 inventory 与单 proposition 拆分 lane。
    """

    CLAIM_INVENTORY_REPAIR = "claim_inventory_repair"
    CLAIM_PROPOSITION_REPAIR = "claim_proposition_repair"


class SemanticRepairPlanRoute(StrEnum):
    """表示 M08 审查聚合经过 M09 规划后的业务路由。

    :return: 无返回值；该路由不是任务执行终态或 artifact verified 状态。
    """

    NO_REPAIR_REQUIRED = "no_repair_required"
    REPAIR_REQUIRED = "repair_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    REPAIR_THEN_CLARIFICATION_REQUIRED = "repair_then_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DISAGREEMENT = "disagreement"
    REVIEW_FAILED = "review_failed"


class SemanticRepairHumanReviewReason(StrEnum):
    """表示 M09 保持人工审查或不可自动修复的稳定原因。

    :return: 无返回值；该枚举防止未知问题被自动归入已知修复维度。
    """

    UNCLASSIFIED_COVERAGE_ISSUE = "unclassified_coverage_issue"
    UNCLASSIFIED_SEMANTIC_CHANGE = "unclassified_semantic_change"
    REVIEW_DISAGREEMENT = "review_disagreement"
    REVIEW_FAILED = "review_failed"
    REPAIR_BUDGET_EXCEEDED = "repair_budget_exceeded"
    HUMAN_REVIEW_REQUESTED = "human_review_requested"


class SemanticRepairStaleReason(StrEnum):
    """表示既有下游审查结果失效的确定性原因。

    :return: 无返回值；stale 记录不能迁移为新 claim 的审查结论。
    """

    CLAIM_INVENTORY_REPAIR_REQUIRED = "claim_inventory_repair_required"


class SemanticRepairPlanVerificationState(StrEnum):
    """表示 M09 修复计划的确定性验证结论。

    :return: 无返回值；blocked 计划不得进入 M10。
    """

    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class SemanticRepairPlanFailureCode(StrEnum):
    """表示 M09 修复计划验证失败的稳定错误编码。

    :return: 无返回值；未知编码不得进入修复执行。
    """

    IDENTITY_MISMATCH = "identity_mismatch"
    INVALID_REVIEW_BUNDLE = "invalid_review_bundle"
    INVALID_REPAIR_LANE = "invalid_repair_lane"
    INVALID_DIMENSION_ROUTING = "invalid_dimension_routing"
    INVALID_GAP_ROUTING = "invalid_gap_routing"
    BUDGET_EXCEEDED = "budget_exceeded"
    PLAN_PAYLOAD_MISMATCH = "plan_payload_mismatch"


class SemanticRepairPlannerPolicy(BaseModel):
    """表示 M09 通用修复计划的固定预算策略。

    :return: 无返回值；策略只控制任务数量和修复深度，不引入维度级规则分支。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_depth: int = Field(
        default=1,
        ge=1,
        le=1,
        description="允许的修复深度；当前生产禁止 repair of repair。",
    )
    max_repair_dimensions_per_target: int = Field(
        default=2,
        ge=1,
        le=2,
        description="单个 inventory 或 proposition 修复任务携带的 true 维度上限。",
    )
    max_claim_proposition_repair_tasks: int = Field(
        default=2,
        ge=1,
        le=2,
        description="单轮允许同时执行的单 claim 修复任务上限。",
    )
    max_total_repair_tasks: int = Field(
        default=2,
        ge=1,
        le=2,
        description="单轮 M09 计划允许的修复任务总量上限。",
    )

    @model_validator(mode="after")
    def validate_task_budgets(self) -> Self:
        """校验任务级预算不超过全局预算。

        :return: 返回通过预算闭合校验的 M09 策略。
        :raises ValueError: claim 级预算超过全局预算时抛出。
        """
        if self.max_claim_proposition_repair_tasks > self.max_total_repair_tasks:
            raise ValueError("claim repair budget exceeds total repair budget")
        return self


class SemanticRepairTask(BaseModel):
    """表示一个由 M09 创建的通用修复任务。

    :return: 无返回值；该任务不是 patch proposal，也不能直接修改 artifact。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_task_id: str = Field(
        min_length=1,
        max_length=360,
        description="由来源身份、lane 与 review 维度派生的稳定任务标识。",
    )
    repair_lane: SemanticRepairLane
    repair_skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="M10 将消费的已注册通用 Repair SKILL 标识。",
    )
    repair_skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="M10 将消费的精确 Repair SKILL 版本。",
    )
    run_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=360)
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_dimensions: tuple[ReviewRepairDimension, ...] = Field(
        min_length=1,
        description="当前任务需要交给通用 Repair LLM 处理的 M08 true 维度。",
    )
    target_claim_index: int | None = Field(
        default=None,
        ge=0,
        description="单 proposition 修复目标的确定性 claim 序号。",
    )
    target_claim_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    target_claim_proposition: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
    )
    repair_hints: tuple[str, ...] = Field(
        default=(),
        max_length=8,
        description="Coverage Review 提供的非权威修复线索。",
    )
    depends_on_review_task_ids: tuple[str, ...] = Field(
        min_length=1,
        description="当前修复任务依赖的 M08 审查任务标识。",
    )

    @model_validator(mode="after")
    def validate_lane_payload(self) -> Self:
        """校验修复 lane 与目标、维度和 hint 负载闭合。

        :return: 返回通过 lane 契约校验的修复任务。
        :raises ValueError: lane、claim 目标或维度类型不匹配时抛出。
        """
        if self.repair_lane is SemanticRepairLane.CLAIM_INVENTORY_REPAIR:
            if (
                self.target_claim_index is not None
                or self.target_claim_digest is not None
                or self.target_claim_proposition is not None
                or not all(
                    isinstance(dimension, ClaimCoverageReviewDimension)
                    for dimension in self.review_dimensions
                )
            ):
                raise ValueError("claim inventory repair payload is invalid")
            return self
        if (
            self.target_claim_index is None
            or self.target_claim_digest is None
            or self.target_claim_proposition is None
            or self.repair_hints
            or not all(
                isinstance(dimension, ClaimFaithfulnessReviewDimension)
                for dimension in self.review_dimensions
            )
        ):
            raise ValueError("claim proposition repair payload is invalid")
        return self


class SuppressedClaimRepair(BaseModel):
    """表示因 inventory 修复优先而被抑制的单 claim 修复目标。

    :return: 无返回值；该记录用于保留审计线索，不是待执行任务。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    claim_index: int = Field(ge=0)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_proposition: str = Field(min_length=1, max_length=240)
    repair_dimensions: tuple[ClaimFaithfulnessReviewDimension, ...] = Field(
        min_length=1,
    )
    source_review_task_id: str = Field(min_length=1, max_length=360)
    stale_reason: SemanticRepairStaleReason


class SemanticRepairPlan(BaseModel):
    """表示一次 M08 Review Bundle 的确定性 M09 修复计划。

    :return: 无返回值；该计划不包含修正文本或 patch operations。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    plan_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    route: SemanticRepairPlanRoute
    run_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=360)
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: SemanticRepairPlannerPolicy
    repair_tasks: tuple[SemanticRepairTask, ...] = Field(
        default=(),
        max_length=2,
    )
    active_clarification_gaps: tuple[ClarificationGapProposal, ...] = Field(
        default=(),
    )
    stale_clarification_gaps: tuple[ClarificationGapProposal, ...] = Field(
        default=(),
    )
    human_review_reasons: tuple[SemanticRepairHumanReviewReason, ...] = Field(
        default=(),
    )
    stale_review_task_ids: tuple[str, ...] = Field(
        default=(),
        description="因 inventory 修复而失效的既有 Faithfulness 审查任务。",
    )
    suppressed_claim_repairs: tuple[SuppressedClaimRepair, ...] = Field(
        default=(),
    )

    @model_validator(mode="after")
    def validate_route_payload(self) -> Self:
        """校验计划路由与任务、gap、人工审查负载闭合。

        :return: 返回通过路由状态机校验的修复计划。
        :raises ValueError: 路由与负载组合非法时抛出。
        """
        no_repair_routes = {
            SemanticRepairPlanRoute.NO_REPAIR_REQUIRED,
            SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED,
            SemanticRepairPlanRoute.DISAGREEMENT,
            SemanticRepairPlanRoute.REVIEW_FAILED,
        }
        if self.route in no_repair_routes and self.repair_tasks:
            raise ValueError("non-repair route cannot carry repair tasks")
        if self.route is SemanticRepairPlanRoute.CLARIFICATION_REQUIRED and (
            self.repair_tasks or not self.active_clarification_gaps
        ):
            raise ValueError("clarification route payload is invalid")
        if self.route is SemanticRepairPlanRoute.REPAIR_REQUIRED and (
            not self.repair_tasks or self.active_clarification_gaps
        ):
            raise ValueError("repair route payload is invalid")
        if (
            self.route is SemanticRepairPlanRoute.REPAIR_THEN_CLARIFICATION_REQUIRED
            and (not self.repair_tasks or not self.active_clarification_gaps)
        ):
            raise ValueError("repair then clarification route payload is invalid")
        if self.route is SemanticRepairPlanRoute.NO_REPAIR_REQUIRED and (
            self.active_clarification_gaps
            or self.stale_clarification_gaps
            or self.human_review_reasons
            or self.stale_review_task_ids
            or self.suppressed_claim_repairs
        ):
            raise ValueError("no-repair route cannot carry review side effects")
        if len({task.repair_task_id for task in self.repair_tasks}) != len(
            self.repair_tasks,
        ):
            raise ValueError("duplicate repair task identity")
        return self


class SemanticRepairPlanVerificationResult(BaseModel):
    """表示 M09 修复计划的确定性验证结果。

    :return: 无返回值；blocked 计划不得交给 M10。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    state: SemanticRepairPlanVerificationState
    failure_code: SemanticRepairPlanFailureCode | None = None
    failure_message: str | None = Field(
        default=None,
        max_length=1000,
        description="面向工程排障的失败说明，不包含用户原文。",
    )

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验计划验证结果与失败负载闭合。

        :return: 返回通过显式终态校验的验证结果。
        :raises ValueError: accepted 带失败或 blocked 缺失败时抛出。
        """
        if self.state is SemanticRepairPlanVerificationState.ACCEPTED:
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("accepted repair plan cannot carry failure")
            return self
        if self.failure_code is None or not self.failure_message:
            raise ValueError("blocked repair plan requires failure")
        return self


def compute_review_bundle_digest(bundle: SemanticReviewBundle) -> str:
    """计算 M08 Review Bundle 的 canonical SHA-256 摘要。

    :param bundle: 已完成 M08 验证与确定性派生的审查聚合。
    :return: 返回 M09 / M10 / M11 共用的权威审查身份。
    """
    return sha256(
        bundle.model_dump_json(by_alias=True).encode("utf-8"),
    ).hexdigest()


def compute_repair_task_hash(
    source_task_id: str,
    review_bundle_digest: str,
    repair_lane: SemanticRepairLane,
    claim_index: int | None,
    claim_digest: str | None,
    review_dimensions: tuple[ReviewRepairDimension, ...],
) -> str:
    """计算通用修复任务的确定性身份片段。

    :param source_task_id: 被审查的 Claim Inventory 任务标识。
    :param review_bundle_digest: 当前 M08 Review Bundle 的 canonical 摘要。
    :param repair_lane: 当前通用修复上下文粒度。
    :param claim_index: 单 proposition 修复的 claim 序号；inventory 修复为空。
    :param claim_digest: 单 proposition 修复的 claim 摘要；inventory 修复为空。
    :param review_dimensions: 当前任务携带的 M08 true 维度集合。
    :return: 返回 16 位十六进制稳定身份片段。
    """
    identity = "\0".join(
        (
            source_task_id,
            review_bundle_digest,
            repair_lane.value,
            "" if claim_index is None else str(claim_index),
            claim_digest or "",
            *(dimension.value for dimension in review_dimensions),
        ),
    )
    return sha256(identity.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ReviewRepairDimension",
    "SemanticRepairHumanReviewReason",
    "SemanticRepairLane",
    "SemanticRepairPlan",
    "SemanticRepairPlanFailureCode",
    "SemanticRepairPlanRoute",
    "SemanticRepairPlanVerificationResult",
    "SemanticRepairPlanVerificationState",
    "SemanticRepairPlannerPolicy",
    "SemanticRepairStaleReason",
    "SemanticRepairTask",
    "SuppressedClaimRepair",
    "compute_repair_task_hash",
    "compute_review_bundle_digest",
]
