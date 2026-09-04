"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/patch_verifier.py
作用：实现受限语义协作 DAG M10 的 typed patch 与 patch set 确定性验证器。
范围：覆盖 M09 任务闭合、M11 base 快照身份、lane/schema 负载、目标 digest、
      operation 预算、最终 claims 预览、patch set 原子冲突与显式 blocked 状态。
说明：本文件不调用 LLM、不读取原始用户文本、不做医学判断、不提交 artifact，
      也不把 accepted patch 解释为语义修复成功或 verified。
=============================================================================
"""

from __future__ import annotations

from .patch_applier import DeterministicPatchApplier
from .patch_contracts import (
    PatchApplicationState,
    RepairTargetArtifactSnapshot,
    SemanticPatchFailureCode,
    SemanticPatchOperationType,
    SemanticPatchProposal,
    SemanticPatchSet,
    SemanticPatchVerificationResult,
    SemanticPatchVerificationState,
    compute_patch_id,
)
from .repair_contracts import (
    SemanticRepairLane,
    SemanticRepairPlan,
    SemanticRepairPlannerPolicy,
)
from .review_contracts import compute_claim_digest


class SemanticPatchVerifier:
    """表示单个 M10 typed patch proposal 的确定性验证器。

    :return: 无返回值；accepted 只表示结构与身份闭合，不表示语义修复成功。
    """

    def verify(
        self,
        proposal: SemanticPatchProposal,
        *,
        plan: SemanticRepairPlan,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> SemanticPatchVerificationResult:
        """验证单个 patch 与 M09 plan 和 M11 snapshot 完全闭合。

        :param proposal: 系统 compiler 生成的 typed patch proposal。
        :param plan: 产生该 patch 的 accepted M09 repair plan。
        :param snapshot: M11 base artifact 快照。
        :return: 返回 accepted 或 blocked 的显式验证结果。
        """
        task = proposal.repair_task
        if (
            compute_patch_id(proposal) != proposal.patch_id
            or proposal.repair_plan_id != plan.plan_id
            or task not in plan.repair_tasks
        ):
            return self._blocked(
                SemanticPatchFailureCode.IDENTITY_MISMATCH,
                "semantic patch task is absent from repair plan",
            )
        if (
            proposal.source_proposal_digest,
            proposal.review_bundle_digest,
            proposal.turn_snapshot_digest,
            proposal.artifact_reference,
            proposal.base_version,
        ) != (
            snapshot.source_proposal_digest,
            snapshot.review_bundle_digest,
            snapshot.turn_snapshot_digest,
            snapshot.artifact_reference,
            snapshot.base_version,
        ):
            return self._blocked(
                SemanticPatchFailureCode.IDENTITY_MISMATCH,
                "semantic patch snapshot identity mismatch",
            )
        if snapshot.repair_depth != 0:
            return self._blocked(
                SemanticPatchFailureCode.REPAIR_DEPTH_EXCEEDED,
                "repair target is already a repair output",
            )
        if len(proposal.operations) > 2:
            return self._blocked(
                SemanticPatchFailureCode.BUDGET_EXCEEDED,
                "semantic patch operation budget exceeded",
            )
        if not self._lane_operations_are_closed(proposal):
            return self._blocked(
                SemanticPatchFailureCode.RESULT_INVALID,
                "semantic patch lane operations are invalid",
            )
        if not self._targets_match_snapshot(proposal, snapshot):
            return self._blocked(
                SemanticPatchFailureCode.TARGET_MISMATCH,
                "semantic patch target digest mismatch",
            )
        preview = DeterministicPatchApplier().preview(
            SemanticPatchSet(
                repair_plan_id=proposal.repair_plan_id,
                patches=(proposal,),
                artifact_reference=proposal.artifact_reference,
                base_version=proposal.base_version,
            ),
            snapshot,
        )
        if preview.state is not PatchApplicationState.PREVIEW_READY:
            return self._blocked(
                preview.failure_code or SemanticPatchFailureCode.RESULT_INVALID,
                preview.failure_message or "semantic patch result is invalid",
            )
        if preview.claims == snapshot.claims:
            return self._blocked(
                SemanticPatchFailureCode.RESULT_INVALID,
                "semantic patch does not change claim inventory",
            )
        return SemanticPatchVerificationResult(
            state=SemanticPatchVerificationState.ACCEPTED,
        )

    def _lane_operations_are_closed(
        self,
        proposal: SemanticPatchProposal,
    ) -> bool:
        """校验修复 lane 与 typed operations 的形态闭合。

        :param proposal: 当前待验证的 patch proposal。
        :return: lane 与 operation 集合完全匹配时返回 True。
        """
        if proposal.repair_lane is SemanticRepairLane.CLAIM_PROPOSITION_REPAIR:
            operation = proposal.operations[0]
            return (
                len(proposal.operations) == 1
                and operation.operation is SemanticPatchOperationType.REPLACE_CLAIM
                and operation.target_claim_index
                == proposal.repair_task.target_claim_index
            )
        return bool(proposal.operations)

    def _targets_match_snapshot(
        self,
        proposal: SemanticPatchProposal,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> bool:
        """校验全部目标 claim index 与 digest 均来自 base snapshot。

        :param proposal: 当前待验证的 patch proposal。
        :param snapshot: M11 base artifact 快照。
        :return: 全部目标闭合时返回 True。
        """
        for operation in proposal.operations:
            index = operation.target_claim_index
            digest = operation.base_claim_digest
            if index is None:
                if digest is not None:
                    return False
                expected_after_index = (
                    len(snapshot.claims) - 1 if snapshot.claims else None
                )
                if operation.after_claim_index != expected_after_index:
                    return False
                continue
            if index >= len(snapshot.claims):
                return False
            if digest is None or compute_claim_digest(snapshot.claims[index]) != digest:
                return False
            if (
                operation.operation is SemanticPatchOperationType.REPLACE_CLAIM
                and operation.proposition == snapshot.claims[index]
            ):
                return False
        return True

    def _blocked(
        self,
        failure_code: SemanticPatchFailureCode,
        failure_message: str,
    ) -> SemanticPatchVerificationResult:
        """构造显式 blocked patch 验证结果。

        :param failure_code: 稳定 patch 失败码。
        :param failure_message: 面向工程排障的失败说明。
        :return: 返回 blocked 验证结果。
        """
        return SemanticPatchVerificationResult(
            state=SemanticPatchVerificationState.BLOCKED,
            failure_code=failure_code,
            failure_message=failure_message,
        )


class SemanticPatchSetVerifier:
    """表示同一 M09 plan 下 typed patch set 的确定性验证器。

    :return: 无返回值；验证器确保 patch set 覆盖全部修复任务且可原子应用。
    """

    def __init__(
        self,
        *,
        patch_verifier: SemanticPatchVerifier | None = None,
        policy: SemanticRepairPlannerPolicy | None = None,
    ) -> None:
        """初始化 patch set 验证器与 M09 预算策略。

        :param patch_verifier: 可选单 patch 验证器；为空时使用生产默认实例。
        :param policy: 可选 M09 修复预算策略；为空时使用生产默认策略。
        :return: 无返回值。
        """
        self.patch_verifier = patch_verifier or SemanticPatchVerifier()
        self.policy = policy or SemanticRepairPlannerPolicy()

    def verify(
        self,
        patch_set: SemanticPatchSet,
        *,
        plan: SemanticRepairPlan,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> SemanticPatchVerificationResult:
        """验证 patch set 覆盖 M09 plan 且不存在原子应用冲突。

        :param patch_set: 同一 base version 上的 typed patch 集合。
        :param plan: 产生 patch set 的 accepted M09 repair plan。
        :param snapshot: M11 base artifact 快照。
        :return: 返回 accepted 或 blocked 的显式验证结果。
        """
        if patch_set.repair_plan_id != plan.plan_id:
            return SemanticPatchVerificationResult(
                state=SemanticPatchVerificationState.BLOCKED,
                failure_code=SemanticPatchFailureCode.IDENTITY_MISMATCH,
                failure_message="patch set plan identity mismatch",
            )
        if len(patch_set.patches) != len(plan.repair_tasks):
            return SemanticPatchVerificationResult(
                state=SemanticPatchVerificationState.BLOCKED,
                failure_code=SemanticPatchFailureCode.PATCH_CONFLICT,
                failure_message="patch set does not cover repair plan tasks",
            )
        plan_task_ids = {task.repair_task_id for task in plan.repair_tasks}
        patch_task_ids = {
            patch.repair_task.repair_task_id for patch in patch_set.patches
        }
        if plan_task_ids != patch_task_ids:
            return SemanticPatchVerificationResult(
                state=SemanticPatchVerificationState.BLOCKED,
                failure_code=SemanticPatchFailureCode.PATCH_CONFLICT,
                failure_message="patch set task identity mismatch",
            )
        if len(patch_set.patches) > self.policy.max_total_repair_tasks:
            return SemanticPatchVerificationResult(
                state=SemanticPatchVerificationState.BLOCKED,
                failure_code=SemanticPatchFailureCode.BUDGET_EXCEEDED,
                failure_message="patch set task budget exceeded",
            )
        for patch in patch_set.patches:
            result = self.patch_verifier.verify(
                patch,
                plan=plan,
                snapshot=snapshot,
            )
            if result.state is not SemanticPatchVerificationState.ACCEPTED:
                return result
        preview = DeterministicPatchApplier().preview(patch_set, snapshot)
        if preview.state is not PatchApplicationState.PREVIEW_READY:
            return SemanticPatchVerificationResult(
                state=SemanticPatchVerificationState.BLOCKED,
                failure_code=preview.failure_code
                or SemanticPatchFailureCode.RESULT_INVALID,
                failure_message=preview.failure_message
                or "patch set application preview is invalid",
            )
        return SemanticPatchVerificationResult(
            state=SemanticPatchVerificationState.ACCEPTED,
        )


__all__ = [
    "SemanticPatchSetVerifier",
    "SemanticPatchVerifier",
]
