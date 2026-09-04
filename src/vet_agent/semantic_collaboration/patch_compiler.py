"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/patch_compiler.py
作用：实现受限语义协作 DAG M10 的确定性 typed patch compiler。
范围：覆盖单 proposition 修复包装、inventory 稀疏 delta 编译、局部 selector
      解析、base claim digest 附加、系统 patch 信封构造与 patch id 派生。
说明：本文件不调用 LLM、不做医学判断、不应用 patch、不写 M11 artifact，
      也不让模型输出 operation、base version 或 artifact 身份。
=============================================================================
"""

from __future__ import annotations

from hashlib import sha256

from .errors import SemanticRepairPlanError
from .gateway_contracts import StructuredLLMCallMetadata
from .patch_contracts import (
    ClaimInventoryRepairOutput,
    ClaimPropositionRepairOutput,
    RepairTargetArtifactSnapshot,
    SemanticPatchOperation,
    SemanticPatchOperationType,
    SemanticPatchPolicy,
    SemanticPatchProposal,
    compute_model_output_digest,
    compute_patch_id,
)
from .repair_contracts import SemanticRepairLane, SemanticRepairTask
from .review_contracts import compute_claim_digest


class SemanticPatchCompiler:
    """表示 M10 模型语义输出到 typed patch proposal 的确定性编译器。

    :return: 无返回值；编译器只生成系统信封，不生成新的权威 artifact。
    """

    def __init__(
        self,
        policy: SemanticPatchPolicy | None = None,
    ) -> None:
        """初始化 M10 typed patch 编译策略。

        :param policy: 可选 patch 预算策略；为空时使用生产默认策略。
        :return: 无返回值。
        """
        self.policy = policy or SemanticPatchPolicy()

    def compile_proposition_repair(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        snapshot: RepairTargetArtifactSnapshot,
        output: ClaimPropositionRepairOutput,
        model_metadata: StructuredLLMCallMetadata,
    ) -> SemanticPatchProposal:
        """编译单条 claim 修复模型输出为 replace operation。

        :param repair_plan_id: M09 accepted repair plan 的稳定身份。
        :param task: M09 生成的单 proposition 修复任务。
        :param snapshot: M11 提供的 base artifact 快照。
        :param output: Repair LLM 的极薄 proposition 输出。
        :param model_metadata: M05 返回的单次结构化调用审计元数据。
        :return: 返回系统信封闭合的 typed patch proposal。
        :raises SemanticRepairPlanError: lane、目标或上下文身份非法时抛出。
        """
        self._validate_common_identity(
            repair_plan_id=repair_plan_id,
            task=task,
            snapshot=snapshot,
            expected_lane=SemanticRepairLane.CLAIM_PROPOSITION_REPAIR,
        )
        target_index = task.target_claim_index
        if target_index is None or target_index >= len(snapshot.claims):
            raise SemanticRepairPlanError("proposition repair target is unavailable")
        base_claim = snapshot.claims[target_index]
        if compute_claim_digest(base_claim) != task.target_claim_digest:
            raise SemanticRepairPlanError("proposition repair target digest mismatch")
        operation = SemanticPatchOperation(
            operation=SemanticPatchOperationType.REPLACE_CLAIM,
            target_claim_index=target_index,
            base_claim_digest=task.target_claim_digest,
            proposition=output.proposition,
        )
        return self._build_proposal(
            repair_plan_id=repair_plan_id,
            task=task,
            snapshot=snapshot,
            operations=(operation,),
            model_output_digest=compute_model_output_digest(output),
            model_metadata=model_metadata,
        )

    def compile_inventory_repair(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        snapshot: RepairTargetArtifactSnapshot,
        output: ClaimInventoryRepairOutput,
        model_metadata: StructuredLLMCallMetadata,
    ) -> SemanticPatchProposal:
        """编译 inventory 稀疏 delta 为系统 typed operations。

        :param repair_plan_id: M09 accepted repair plan 的稳定身份。
        :param task: M09 生成的 inventory 修复任务。
        :param snapshot: M11 提供的 base artifact 快照。
        :param output: Repair LLM 的稀疏 modified/added claims 输出。
        :param model_metadata: M05 返回的单次结构化调用审计元数据。
        :return: 返回系统信封闭合的 typed patch proposal。
        :raises SemanticRepairPlanError: selector、预算或上下文身份非法时抛出。
        """
        self._validate_common_identity(
            repair_plan_id=repair_plan_id,
            task=task,
            snapshot=snapshot,
            expected_lane=SemanticRepairLane.CLAIM_INVENTORY_REPAIR,
        )
        operations: list[SemanticPatchOperation] = []
        for update in output.modified_claims:
            target_index = self._selector_index(update.target)
            if target_index >= len(snapshot.claims):
                raise SemanticRepairPlanError("inventory repair target is unavailable")
            base_digest = compute_claim_digest(snapshot.claims[target_index])
            if not update.propositions:
                operations.append(
                    SemanticPatchOperation(
                        operation=SemanticPatchOperationType.REMOVE_CLAIM,
                        target_claim_index=target_index,
                        base_claim_digest=base_digest,
                    ),
                )
            elif len(update.propositions) == 1:
                operations.append(
                    SemanticPatchOperation(
                        operation=SemanticPatchOperationType.REPLACE_CLAIM,
                        target_claim_index=target_index,
                        base_claim_digest=base_digest,
                        proposition=update.propositions[0],
                    ),
                )
            else:
                if len(update.propositions) > self.policy.max_split_propositions:
                    raise SemanticRepairPlanError("claim split exceeds patch budget")
                operations.append(
                    SemanticPatchOperation(
                        operation=SemanticPatchOperationType.REPLACE_CLAIM_WITH_CLAIMS,
                        target_claim_index=target_index,
                        base_claim_digest=base_digest,
                        propositions=update.propositions,
                    ),
                )
        after_claim_index = len(snapshot.claims) - 1 if snapshot.claims else None
        for proposition in output.added_claims:
            operations.append(
                SemanticPatchOperation(
                    operation=SemanticPatchOperationType.ADD_CLAIM,
                    proposition=proposition,
                    after_claim_index=after_claim_index,
                ),
            )
        if not operations or len(operations) > self.policy.max_operations_per_patch:
            raise SemanticRepairPlanError("inventory patch operation budget exceeded")
        return self._build_proposal(
            repair_plan_id=repair_plan_id,
            task=task,
            snapshot=snapshot,
            operations=tuple(operations),
            model_output_digest=compute_model_output_digest(output),
            model_metadata=model_metadata,
        )

    def _validate_common_identity(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        snapshot: RepairTargetArtifactSnapshot,
        expected_lane: SemanticRepairLane,
    ) -> None:
        """校验 M09 task、M11 snapshot 与期望修复 lane 身份闭合。

        :param repair_plan_id: M09 repair plan 的稳定身份。
        :param task: 当前待编译的 M09 修复任务。
        :param snapshot: M11 base artifact 快照。
        :param expected_lane: 当前 compiler 方法期望的通用修复 lane。
        :return: 无返回值。
        :raises SemanticRepairPlanError: plan、lane、digest 或版本身份不一致时抛出。
        """
        if len(repair_plan_id) != 64:
            raise SemanticRepairPlanError("repair plan identity is invalid")
        if task.repair_lane is not expected_lane:
            raise SemanticRepairPlanError("repair lane does not match compiler")
        if (
            task.source_proposal_digest,
            task.review_bundle_digest,
            task.turn_snapshot_digest,
        ) != (
            snapshot.source_proposal_digest,
            snapshot.review_bundle_digest,
            snapshot.turn_snapshot_digest,
        ):
            raise SemanticRepairPlanError("repair target snapshot identity mismatch")

    def _selector_index(
        self,
        selector: str,
    ) -> int:
        """解析任务内局部 claim selector 的数组序号。

        :param selector: c0 到 c7 的局部 selector。
        :return: 返回对应 base claim 的确定性数组序号。
        :raises SemanticRepairPlanError: selector 非法时抛出。
        """
        try:
            index = int(selector[1:])
        except ValueError as error:
            raise SemanticRepairPlanError("claim selector is invalid") from error
        if index < 0 or index > 7:
            raise SemanticRepairPlanError("claim selector is out of range")
        return index

    def _build_proposal(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        snapshot: RepairTargetArtifactSnapshot,
        operations: tuple[SemanticPatchOperation, ...],
        model_output_digest: str,
        model_metadata: StructuredLLMCallMetadata,
    ) -> SemanticPatchProposal:
        """构造系统信封并派生稳定 patch id。

        :param repair_plan_id: M09 repair plan 的稳定身份。
        :param task: 当前 M09 修复任务。
        :param snapshot: M11 base artifact 快照。
        :param operations: 系统确定性编译出的 typed operations。
        :param model_output_digest: Repair 模型语义输出摘要。
        :param model_metadata: M05 模型调用审计元数据。
        :return: 返回身份闭合且带 canonical patch id 的 proposal。
        """
        proposal = SemanticPatchProposal(
            patch_id=sha256(b"semantic-patch-placeholder").hexdigest(),
            repair_plan_id=repair_plan_id,
            repair_task=task,
            repair_lane=task.repair_lane,
            repair_skill_id=task.repair_skill_id,
            repair_skill_version=task.repair_skill_version,
            run_id=task.run_id,
            turn_snapshot_digest=task.turn_snapshot_digest,
            source_proposal_digest=task.source_proposal_digest,
            review_bundle_digest=task.review_bundle_digest,
            artifact_reference=snapshot.artifact_reference,
            base_version=snapshot.base_version,
            repair_dimensions=task.review_dimensions,
            operations=operations,
            model_output_digest=model_output_digest,
            model_metadata=model_metadata,
        )
        return proposal.model_copy(update={"patch_id": compute_patch_id(proposal)})


__all__ = ["SemanticPatchCompiler"]
