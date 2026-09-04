"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/patch_applier.py
作用：实现受限语义协作 DAG M10 的确定性 patch 应用预览器。
范围：覆盖 typed operations 到 claim inventory 的原子应用预览、未申报 claim
      原样保留、新增 claim 追加、最终形态校验与 M11 next version 派生。
说明：本文件不调用 LLM、不提交数据库 artifact、不做语义审查、不生成
      verified 状态，也不允许自由 JSON Patch。
=============================================================================
"""

from __future__ import annotations

from .patch_contracts import (
    PatchApplicationPreview,
    PatchApplicationState,
    RepairTargetArtifactSnapshot,
    SemanticPatchFailureCode,
    SemanticPatchOperation,
    SemanticPatchOperationType,
    SemanticPatchSet,
)


def apply_operations_to_claims(
    claims: tuple[str, ...],
    operations: tuple[SemanticPatchOperation, ...],
) -> tuple[str, ...]:
    """将 typed operations 确定性应用为新的 claim 序列预览。

    :param claims: M11 base artifact 中的权威 claim 序列。
    :param operations: 已通过 deterministic compiler 生成的 typed operations。
    :return: 返回应用后的 claim 序列预览。
    :raises ValueError: 目标重复、目标越界、最终数量超限或 proposition 非法时抛出。
    """
    operations_by_index = {
        operation.target_claim_index: operation
        for operation in operations
        if operation.target_claim_index is not None
    }
    if len(operations_by_index) != sum(
        operation.target_claim_index is not None for operation in operations
    ):
        raise ValueError("patch target is duplicate")
    result: list[str] = []
    for index, claim in enumerate(claims):
        operation = operations_by_index.get(index)
        if operation is None:
            result.append(claim)
        elif operation.operation is SemanticPatchOperationType.REMOVE_CLAIM:
            continue
        elif operation.operation is SemanticPatchOperationType.REPLACE_CLAIM:
            proposition = operation.proposition
            if proposition is None:
                raise ValueError("replace operation proposition is missing")
            result.append(proposition)
        else:
            propositions = operation.propositions
            if propositions is None:
                raise ValueError("split operation propositions are missing")
            result.extend(propositions)
    for operation in operations:
        if operation.operation is SemanticPatchOperationType.ADD_CLAIM:
            proposition = operation.proposition
            if proposition is None:
                raise ValueError("add operation proposition is missing")
            result.append(proposition)
    if len(result) > 8:
        raise ValueError("patch result exceeds claim count limit")
    if len(result) != len(set(result)):
        raise ValueError("patch result contains duplicate claims")
    if any(
        not proposition.strip()
        or proposition != proposition.strip()
        or "\n" in proposition
        or "\r" in proposition
        for proposition in result
    ):
        raise ValueError("patch result proposition is invalid")
    return tuple(result)


class DeterministicPatchApplier:
    """表示 M10 typed patch set 的确定性应用预览器。

    :return: 无返回值；预览必须提交 M11 后才成为权威新版本。
    """

    def preview(
        self,
        patch_set: SemanticPatchSet,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> PatchApplicationPreview:
        """对同一 base version 的 patch set 生成原子应用预览。

        :param patch_set: 已通过 set verifier 的 typed patch 集合。
        :param snapshot: M11 base artifact 快照。
        :return: 返回 ready 或 blocked 的应用预览。
        """
        if (
            patch_set.artifact_reference != snapshot.artifact_reference
            or patch_set.base_version != snapshot.base_version
        ):
            return PatchApplicationPreview(
                state=PatchApplicationState.BLOCKED,
                repair_plan_id=patch_set.repair_plan_id,
                base_artifact_reference=snapshot.artifact_reference,
                base_version=snapshot.base_version,
                next_version=snapshot.base_version,
                claims=snapshot.claims,
                failure_code=SemanticPatchFailureCode.BASE_VERSION_CONFLICT,
                failure_message="patch set base binding does not match snapshot",
            )
        operations = tuple(
            operation for patch in patch_set.patches for operation in patch.operations
        )
        try:
            claims = apply_operations_to_claims(snapshot.claims, operations)
        except ValueError as error:
            return PatchApplicationPreview(
                state=PatchApplicationState.BLOCKED,
                repair_plan_id=patch_set.repair_plan_id,
                base_artifact_reference=snapshot.artifact_reference,
                base_version=snapshot.base_version,
                next_version=snapshot.base_version,
                claims=snapshot.claims,
                failure_code=SemanticPatchFailureCode.RESULT_INVALID,
                failure_message=str(error),
            )
        return PatchApplicationPreview(
            state=PatchApplicationState.PREVIEW_READY,
            repair_plan_id=patch_set.repair_plan_id,
            base_artifact_reference=snapshot.artifact_reference,
            base_version=snapshot.base_version,
            next_version=snapshot.base_version + 1,
            claims=claims,
        )


__all__ = [
    "DeterministicPatchApplier",
    "apply_operations_to_claims",
]
