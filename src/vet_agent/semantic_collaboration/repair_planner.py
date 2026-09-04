"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/repair_planner.py
作用：实现受限语义协作 DAG M09 的确定性 Repair Planner。
范围：覆盖 M08 审查聚合到通用修复 lane 的路由、Coverage 已知问题统一进入
      inventory 修复、Faithfulness 已知漂移统一进入单 claim 修复、
      clarification gap 透传、人工审查与修复预算控制。
说明：本文件不调用 LLM、不生成 patch operations、不修改 claims、不访问数据库，
      也不把未分类问题、disagreement 或来源绑定缺失路由给 Repair LLM。
=============================================================================
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .catalog import SkillRegistry
from .contracts import SkillSpec, SkillTaskKind
from .errors import SemanticRepairPlanError
from .production import (
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
)
from .repair_contracts import (
    ClaimCoverageReviewDimension,
    ClaimFaithfulnessReviewDimension,
    ReviewRepairDimension,
    SemanticRepairHumanReviewReason,
    SemanticRepairLane,
    SemanticRepairPlan,
    SemanticRepairPlanFailureCode,
    SemanticRepairPlannerPolicy,
    SemanticRepairPlanRoute,
    SemanticRepairPlanVerificationResult,
    SemanticRepairPlanVerificationState,
    SemanticRepairStaleReason,
    SemanticRepairTask,
    SuppressedClaimRepair,
    compute_repair_task_hash,
    compute_review_bundle_digest,
)
from .review_contracts import (
    ClaimFaithfulnessExecutionState,
    ClaimFaithfulnessReviewRecord,
    ClarificationGapProposal,
    SemanticReviewBundle,
    SemanticReviewOutcome,
)

KNOWN_COVERAGE_REPAIR_DIMENSIONS: tuple[ClaimCoverageReviewDimension, ...] = (
    ClaimCoverageReviewDimension.MISSING_EXPLICIT_FACT,
    ClaimCoverageReviewDimension.MULTIPLE_FACTS_MERGED,
    ClaimCoverageReviewDimension.DUPLICATE_CLAIM,
    ClaimCoverageReviewDimension.UNSUPPORTED_CLAIM,
    ClaimCoverageReviewDimension.NON_SELF_CONTAINED_PROPOSITION,
    ClaimCoverageReviewDimension.SHARED_SCOPE_SPLIT_ERROR,
)


class SemanticRepairPlanner:
    """表示 M08 审查结果到通用修复任务的确定性规划器。

    :return: 无返回值；规划器只创建任务，不生成修复内容。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        policy: SemanticRepairPlannerPolicy | None = None,
    ) -> None:
        """初始化绑定生产 Repair SKILL 与固定预算的 M09 规划器。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :param policy: 可选修复预算策略；为空时使用生产默认策略。
        :raises SemanticRepairPlanError: Repair SKILL 缺失或契约非法时抛出。
        :return: 无返回值。
        """
        self.policy = policy or SemanticRepairPlannerPolicy()
        self.inventory_repair_spec = self._resolve_repair_spec(
            registry,
            CLAIM_INVENTORY_REPAIR_SPEC,
        )
        self.proposition_repair_spec = self._resolve_repair_spec(
            registry,
            CLAIM_PROPOSITION_REPAIR_SPEC,
        )

    def plan(
        self,
        bundle: SemanticReviewBundle,
    ) -> SemanticRepairPlan:
        """为一个 M08 Review Bundle 生成确定性修复计划。

        :param bundle: 已完成 Coverage / Faithfulness 审查与结果派生的聚合。
        :return: 返回通用修复任务、gap 路由与人工审查路由组成的不可变计划。
        :raises SemanticRepairPlanError: 审查派生结果缺失或路由契约非法时抛出。
        """
        self._validate_bundle(bundle)
        aggregate_outcome = bundle.aggregate_outcome
        if aggregate_outcome is SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED:
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.NO_REPAIR_REQUIRED,
                human_review_reasons=(),
            )
        if aggregate_outcome is SemanticReviewOutcome.REVIEW_FAILED:
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.REVIEW_FAILED,
                human_review_reasons=(SemanticRepairHumanReviewReason.REVIEW_FAILED,),
            )
        if aggregate_outcome is SemanticReviewOutcome.DISAGREEMENT:
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.DISAGREEMENT,
                human_review_reasons=(
                    SemanticRepairHumanReviewReason.REVIEW_DISAGREEMENT,
                ),
            )
        unclassified_reasons = self._unclassified_reasons(bundle)
        if unclassified_reasons:
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED,
                human_review_reasons=unclassified_reasons,
            )
        if aggregate_outcome is SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED:
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED,
                human_review_reasons=self._human_review_reasons(bundle),
            )
        self._validate_route_prerequisites(bundle)

        coverage_dimensions = self._coverage_repair_dimensions(bundle)
        proposition_candidates = self._proposition_repair_candidates(bundle)
        if self._budget_exceeded(
            coverage_dimensions=coverage_dimensions,
            proposition_candidates=proposition_candidates,
        ):
            return self._terminal_plan(
                bundle=bundle,
                route=SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED,
                human_review_reasons=(
                    SemanticRepairHumanReviewReason.REPAIR_BUDGET_EXCEEDED,
                ),
            )

        repair_tasks: tuple[SemanticRepairTask, ...]
        stale_review_task_ids: tuple[str, ...] = ()
        suppressed_claim_repairs: tuple[SuppressedClaimRepair, ...] = ()
        active_gaps: tuple[ClarificationGapProposal, ...] = ()
        stale_gaps: tuple[ClarificationGapProposal, ...] = ()

        if coverage_dimensions:
            repair_tasks = (
                self._build_inventory_repair_task(bundle, coverage_dimensions),
            )
            stale_review_task_ids = tuple(
                record.review_task_id for record in bundle.faithfulness_reviews
            )
            suppressed_repairs: list[SuppressedClaimRepair] = []
            for record in proposition_candidates:
                if record.derived is None:
                    continue
                suppressed_repairs.append(
                    SuppressedClaimRepair(
                        claim_index=record.claim_index,
                        claim_digest=record.claim_digest,
                        claim_proposition=record.claim_proposition,
                        repair_dimensions=record.derived.repair_dimensions,
                        source_review_task_id=record.review_task_id,
                        stale_reason=(
                            SemanticRepairStaleReason.CLAIM_INVENTORY_REPAIR_REQUIRED
                        ),
                    ),
                )
            suppressed_claim_repairs = tuple(suppressed_repairs)
            stale_gaps = bundle.clarification_gaps
        else:
            repair_tasks = tuple(
                self._build_proposition_repair_task(bundle, record)
                for record in proposition_candidates
            )
            active_gaps = bundle.clarification_gaps

        route = self._derive_route(
            repair_tasks=repair_tasks,
            active_gaps=active_gaps,
        )
        return self._build_plan(
            bundle=bundle,
            route=route,
            repair_tasks=repair_tasks,
            active_clarification_gaps=active_gaps,
            stale_clarification_gaps=stale_gaps,
            human_review_reasons=(),
            stale_review_task_ids=stale_review_task_ids,
            suppressed_claim_repairs=suppressed_claim_repairs,
        )

    def _resolve_repair_spec(
        self,
        registry: SkillRegistry,
        expected_spec: SkillSpec,
    ) -> SkillSpec:
        """解析并校验生产目录中的通用 Repair SKILL。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :return: 返回 M10 后续消费的通用 Repair SkillSpec。
        :raises SemanticRepairPlanError: SKILL 缺失、版本漂移或任务类型非法时抛出。
        """
        try:
            spec = registry.require(
                expected_spec.skill_id,
                expected_spec.skill_version,
            )
        except Exception as error:
            raise SemanticRepairPlanError(
                "semantic repair skill is not registered",
            ) from error
        if spec.task_kind is not SkillTaskKind.REPAIR:
            raise SemanticRepairPlanError(
                "semantic repair skill task kind is invalid",
            )
        return spec

    def _validate_bundle(
        self,
        bundle: SemanticReviewBundle,
    ) -> None:
        """校验 M08 聚合包含确定性派生结果。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 无返回值。
        :raises SemanticRepairPlanError: Coverage 派生结果缺失时抛出。
        """
        if bundle.coverage_review.derived is None:
            raise SemanticRepairPlanError(
                "semantic repair planner requires derived coverage review",
            )

    def _validate_route_prerequisites(
        self,
        bundle: SemanticReviewBundle,
    ) -> None:
        """校验 M08 聚合路由具备进入 M09 后续规划的必要负载。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 无返回值。
        :raises SemanticRepairPlanError: clarification 或 repair 路由缺少对应负载时抛出。
        """
        if (
            bundle.aggregate_outcome is SemanticReviewOutcome.CLARIFICATION_REQUIRED
            and not bundle.clarification_gaps
        ):
            raise SemanticRepairPlanError(
                "clarification review bundle does not contain gaps",
            )
        if (
            bundle.aggregate_outcome
            is SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
            and not bundle.clarification_gaps
        ):
            raise SemanticRepairPlanError(
                "repair then clarification review bundle does not contain gaps",
            )
        if (
            bundle.aggregate_outcome is SemanticReviewOutcome.REPAIR_REQUIRED
            and not self._coverage_repair_dimensions(bundle)
            and not self._proposition_repair_candidates(bundle)
        ):
            raise SemanticRepairPlanError(
                "repair review bundle does not contain repair dimensions",
            )

    def _terminal_plan(
        self,
        *,
        bundle: SemanticReviewBundle,
        route: SemanticRepairPlanRoute,
        human_review_reasons: tuple[SemanticRepairHumanReviewReason, ...],
    ) -> SemanticRepairPlan:
        """构造不进入自动修复的显式终态计划。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :param route: 不可自动修复的业务路由。
        :param human_review_reasons: 需要保留的人工审查原因。
        :return: 返回不携带修复任务的不可变计划。
        """
        return self._build_plan(
            bundle=bundle,
            route=route,
            repair_tasks=(),
            active_clarification_gaps=(
                bundle.clarification_gaps
                if route is not SemanticRepairPlanRoute.NO_REPAIR_REQUIRED
                else ()
            ),
            stale_clarification_gaps=(),
            human_review_reasons=human_review_reasons,
            stale_review_task_ids=(),
            suppressed_claim_repairs=(),
        )

    def _coverage_repair_dimensions(
        self,
        bundle: SemanticReviewBundle,
    ) -> tuple[ClaimCoverageReviewDimension, ...]:
        """读取 Coverage 已知问题并统一映射到 inventory 修复 lane。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 返回可交给通用 Claim Inventory Repair 的 true 维度集合。
        """
        derived = bundle.coverage_review.derived
        if derived is None:
            return ()
        return tuple(
            dimension
            for dimension in derived.true_dimensions
            if dimension in KNOWN_COVERAGE_REPAIR_DIMENSIONS
        )

    def _proposition_repair_candidates(
        self,
        bundle: SemanticReviewBundle,
    ) -> tuple[ClaimFaithfulnessReviewRecord, ...]:
        """读取存在可修复漂移的单 claim 审查记录。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 返回按 claim index 排序的 Faithfulness 审查记录元组。
        """
        return tuple(
            record
            for record in bundle.faithfulness_reviews
            if record.execution_state is ClaimFaithfulnessExecutionState.COMPLETED
            and record.derived is not None
            and record.derived.repair_dimensions
        )

    def _budget_exceeded(
        self,
        *,
        coverage_dimensions: tuple[ClaimCoverageReviewDimension, ...],
        proposition_candidates: tuple[ClaimFaithfulnessReviewRecord, ...],
    ) -> bool:
        """判断当前通用修复任务集合是否超过生产预算。

        :param coverage_dimensions: Coverage 已知修复维度。
        :param proposition_candidates: 存在修复维度的单 claim 记录。
        :return: 超过任务数或单目标维度预算时返回 True。
        """
        if len(coverage_dimensions) > self.policy.max_repair_dimensions_per_target:
            return True
        if coverage_dimensions:
            return False
        if any(
            record.derived is not None
            and len(record.derived.true_dimensions)
            > self.policy.max_repair_dimensions_per_target
            for record in proposition_candidates
        ):
            return True
        if len(proposition_candidates) > (
            self.policy.max_claim_proposition_repair_tasks
        ):
            return True
        total_task_count = int(bool(coverage_dimensions)) + (
            0 if coverage_dimensions else len(proposition_candidates)
        )
        if total_task_count > self.policy.max_total_repair_tasks:
            return True
        return any(
            len(record.derived.repair_dimensions)
            > self.policy.max_repair_dimensions_per_target
            for record in proposition_candidates
            if record.derived is not None
        )

    def _build_inventory_repair_task(
        self,
        bundle: SemanticReviewBundle,
        dimensions: tuple[ClaimCoverageReviewDimension, ...],
    ) -> SemanticRepairTask:
        """构造 Coverage 已知问题统一的 Claim Inventory 修复任务。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :param dimensions: Coverage Review 中为 true 的已知维度。
        :return: 返回绑定通用 Repair SKILL 的 inventory 修复任务。
        """
        review_dimensions: tuple[ReviewRepairDimension, ...] = dimensions
        task_hash = compute_repair_task_hash(
            bundle.source_task_id,
            compute_review_bundle_digest(bundle),
            SemanticRepairLane.CLAIM_INVENTORY_REPAIR,
            None,
            None,
            review_dimensions,
        )
        derived = bundle.coverage_review.derived
        return SemanticRepairTask(
            repair_task_id=(
                f"{bundle.run_id}:repair:{task_hash}:"
                f"{self.inventory_repair_spec.skill_id}:"
                f"{self.inventory_repair_spec.skill_version}"
            ),
            repair_lane=SemanticRepairLane.CLAIM_INVENTORY_REPAIR,
            repair_skill_id=self.inventory_repair_spec.skill_id,
            repair_skill_version=self.inventory_repair_spec.skill_version,
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            source_proposal_digest=bundle.source_proposal_digest,
            review_bundle_digest=compute_review_bundle_digest(bundle),
            turn_snapshot_digest=bundle.turn_snapshot_digest,
            review_dimensions=review_dimensions,
            repair_hints=(() if derived is None else derived.missing_claim_candidates),
            depends_on_review_task_ids=(bundle.coverage_review.review_task_id,),
        )

    def _build_proposition_repair_task(
        self,
        bundle: SemanticReviewBundle,
        record: ClaimFaithfulnessReviewRecord,
    ) -> SemanticRepairTask:
        """构造单条 claim 的通用 proposition 修复任务。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :param record: 当前存在修复维度的 Faithfulness 审查记录。
        :return: 返回只指向一条 proposition 的通用修复任务。
        """
        derived = record.derived
        if derived is None:
            raise SemanticRepairPlanError(
                "semantic repair planner requires derived faithfulness review",
            )
        review_dimensions: tuple[ReviewRepairDimension, ...] = derived.repair_dimensions
        task_hash = compute_repair_task_hash(
            bundle.source_task_id,
            compute_review_bundle_digest(bundle),
            SemanticRepairLane.CLAIM_PROPOSITION_REPAIR,
            record.claim_index,
            record.claim_digest,
            review_dimensions,
        )
        return SemanticRepairTask(
            repair_task_id=(
                f"{bundle.run_id}:repair:{task_hash}:"
                f"{self.proposition_repair_spec.skill_id}:"
                f"{self.proposition_repair_spec.skill_version}"
            ),
            repair_lane=SemanticRepairLane.CLAIM_PROPOSITION_REPAIR,
            repair_skill_id=self.proposition_repair_spec.skill_id,
            repair_skill_version=self.proposition_repair_spec.skill_version,
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            source_proposal_digest=bundle.source_proposal_digest,
            review_bundle_digest=compute_review_bundle_digest(bundle),
            turn_snapshot_digest=bundle.turn_snapshot_digest,
            review_dimensions=review_dimensions,
            target_claim_index=record.claim_index,
            target_claim_digest=record.claim_digest,
            target_claim_proposition=record.claim_proposition,
            depends_on_review_task_ids=(record.review_task_id,),
        )

    def _unclassified_reasons(
        self,
        bundle: SemanticReviewBundle,
    ) -> tuple[SemanticRepairHumanReviewReason, ...]:
        """读取当前聚合中的未分类审查原因。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 返回去重后的稳定人工审查原因集合。
        """
        reasons: list[SemanticRepairHumanReviewReason] = []
        coverage_dimensions = (
            ()
            if bundle.coverage_review.derived is None
            else bundle.coverage_review.derived.true_dimensions
        )
        if ClaimCoverageReviewDimension.UNCLASSIFIED_ISSUE in coverage_dimensions:
            reasons.append(
                SemanticRepairHumanReviewReason.UNCLASSIFIED_COVERAGE_ISSUE,
            )
        if any(
            record.derived is not None
            and (
                ClaimFaithfulnessReviewDimension.UNCLASSIFIED_SEMANTIC_CHANGE
                in record.derived.true_dimensions
            )
            for record in bundle.faithfulness_reviews
        ):
            reasons.append(
                SemanticRepairHumanReviewReason.UNCLASSIFIED_SEMANTIC_CHANGE,
            )
        return tuple(reasons)

    def _human_review_reasons(
        self,
        bundle: SemanticReviewBundle,
    ) -> tuple[SemanticRepairHumanReviewReason, ...]:
        """推导 M08 human review 聚合在 M09 中的稳定原因。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :return: 返回预算超限或显式人工审查原因集合。
        """
        if self._budget_exceeded(
            coverage_dimensions=self._coverage_repair_dimensions(bundle),
            proposition_candidates=self._proposition_repair_candidates(bundle),
        ):
            return (SemanticRepairHumanReviewReason.REPAIR_BUDGET_EXCEEDED,)
        return (SemanticRepairHumanReviewReason.HUMAN_REVIEW_REQUESTED,)

    def _derive_route(
        self,
        *,
        repair_tasks: tuple[SemanticRepairTask, ...],
        active_gaps: tuple[ClarificationGapProposal, ...],
    ) -> SemanticRepairPlanRoute:
        """根据修复任务与活跃 gap 派生计划路由。

        :param repair_tasks: 当前计划中的通用修复任务。
        :param active_gaps: 不受 inventory 修复 stale 影响的 gap 集合。
        :return: 返回 M09 计划业务路由。
        """
        if repair_tasks and active_gaps:
            return SemanticRepairPlanRoute.REPAIR_THEN_CLARIFICATION_REQUIRED
        if repair_tasks:
            return SemanticRepairPlanRoute.REPAIR_REQUIRED
        if active_gaps:
            return SemanticRepairPlanRoute.CLARIFICATION_REQUIRED
        return SemanticRepairPlanRoute.NO_REPAIR_REQUIRED

    def _build_plan(
        self,
        *,
        bundle: SemanticReviewBundle,
        route: SemanticRepairPlanRoute,
        repair_tasks: tuple[SemanticRepairTask, ...],
        active_clarification_gaps: tuple[ClarificationGapProposal, ...],
        stale_clarification_gaps: tuple[ClarificationGapProposal, ...],
        human_review_reasons: tuple[SemanticRepairHumanReviewReason, ...],
        stale_review_task_ids: tuple[str, ...],
        suppressed_claim_repairs: tuple[SuppressedClaimRepair, ...],
    ) -> SemanticRepairPlan:
        """组装带 canonical plan identity 的不可变修复计划。

        :param bundle: 当前待规划的 M08 Review Bundle。
        :param route: 当前计划业务路由。
        :param repair_tasks: 当前计划通用修复任务。
        :param active_clarification_gaps: 仍可被下游消费的 gap 集合。
        :param stale_clarification_gaps: 因 inventory 修复失效的 gap 集合。
        :param human_review_reasons: 人工审查原因集合。
        :param stale_review_task_ids: 因 inventory 修复失效的审查任务。
        :param suppressed_claim_repairs: 因 inventory 修复抑制的单 claim 目标。
        :return: 返回通过契约校验的 M09 修复计划。
        """
        payload: dict[str, Any] = {
            "route": route.value,
            "run_id": bundle.run_id,
            "source_task_id": bundle.source_task_id,
            "source_proposal_digest": bundle.source_proposal_digest,
            "review_bundle_digest": compute_review_bundle_digest(bundle),
            "turn_snapshot_digest": bundle.turn_snapshot_digest,
            "policy": self.policy.model_dump(mode="json"),
            "repair_tasks": [task.model_dump(mode="json") for task in repair_tasks],
            "active_clarification_gaps": [
                gap.model_dump(mode="json") for gap in active_clarification_gaps
            ],
            "stale_clarification_gaps": [
                gap.model_dump(mode="json") for gap in stale_clarification_gaps
            ],
            "human_review_reasons": [reason.value for reason in human_review_reasons],
            "stale_review_task_ids": list(stale_review_task_ids),
            "suppressed_claim_repairs": [
                item.model_dump(mode="json") for item in suppressed_claim_repairs
            ],
        }
        plan_id = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        ).hexdigest()
        return SemanticRepairPlan(
            plan_id=plan_id,
            route=route,
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            source_proposal_digest=bundle.source_proposal_digest,
            review_bundle_digest=compute_review_bundle_digest(bundle),
            turn_snapshot_digest=bundle.turn_snapshot_digest,
            policy=self.policy,
            repair_tasks=repair_tasks,
            active_clarification_gaps=active_clarification_gaps,
            stale_clarification_gaps=stale_clarification_gaps,
            human_review_reasons=human_review_reasons,
            stale_review_task_ids=stale_review_task_ids,
            suppressed_claim_repairs=suppressed_claim_repairs,
        )


class SemanticRepairPlanVerifier:
    """表示 M09 修复计划的确定性复算验证器。

    :return: 无返回值；验证器不解释模型语义，只验证计划与 M08 结果闭合。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        policy: SemanticRepairPlannerPolicy | None = None,
    ) -> None:
        """初始化与生产规划器同构的计划验证器。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :param policy: 可选修复预算策略；为空时使用生产默认策略。
        :return: 无返回值。
        """
        self.planner = SemanticRepairPlanner(
            registry=registry,
            policy=policy,
        )

    def verify(
        self,
        plan: SemanticRepairPlan,
        bundle: SemanticReviewBundle,
    ) -> SemanticRepairPlanVerificationResult:
        """通过确定性复算验证修复计划。

        :param plan: 待进入 M10 的 M09 修复计划。
        :param bundle: 生成该计划的 M08 Review Bundle。
        :return: 返回 accepted 或 blocked 的显式验证结果。
        """
        if (
            plan.run_id,
            plan.source_task_id,
            plan.source_proposal_digest,
            plan.turn_snapshot_digest,
            plan.policy,
        ) != (
            bundle.run_id,
            bundle.source_task_id,
            bundle.source_proposal_digest,
            bundle.turn_snapshot_digest,
            self.planner.policy,
        ):
            return SemanticRepairPlanVerificationResult(
                state=SemanticRepairPlanVerificationState.BLOCKED,
                failure_code=SemanticRepairPlanFailureCode.IDENTITY_MISMATCH,
                failure_message="semantic repair plan identity mismatch",
            )
        try:
            expected_plan = self.planner.plan(bundle)
        except SemanticRepairPlanError:
            return SemanticRepairPlanVerificationResult(
                state=SemanticRepairPlanVerificationState.BLOCKED,
                failure_code=SemanticRepairPlanFailureCode.INVALID_REVIEW_BUNDLE,
                failure_message="semantic review bundle cannot produce repair plan",
            )
        if plan != expected_plan:
            return SemanticRepairPlanVerificationResult(
                state=SemanticRepairPlanVerificationState.BLOCKED,
                failure_code=SemanticRepairPlanFailureCode.PLAN_PAYLOAD_MISMATCH,
                failure_message="semantic repair plan differs from deterministic plan",
            )
        return SemanticRepairPlanVerificationResult(
            state=SemanticRepairPlanVerificationState.ACCEPTED,
        )


__all__ = [
    "KNOWN_COVERAGE_REPAIR_DIMENSIONS",
    "SemanticRepairPlanVerifier",
    "SemanticRepairPlanner",
]
